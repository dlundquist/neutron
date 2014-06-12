# vim: tabstop=4 shiftwidth=4 softtabstop=4
#
# Copyright 2013 New Dream Network, LLC (DreamHost)
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.
#
# @author: Mark McClain, DreamHost

import uuid

from oslo.config import cfg

from neutron.common import constants as q_const
from neutron.common import exceptions as n_exc
from neutron.common import rpc as q_rpc
from neutron.common import rpc_compat
from neutron.common import topics
from neutron.db import agents_db
from neutron.db.loadbalancer import loadbalancer_db
from neutron.extensions import lbaas_agentscheduler
from neutron.extensions import portbindings
from neutron.openstack.common import importutils
from neutron.openstack.common import log as logging
from neutron.openstack.common import rpc
from neutron.plugins.common import constants
from neutron.services.loadbalancer.drivers import abstract_driver

LOG = logging.getLogger(__name__)

AGENT_SCHEDULER_OPTS = [
    cfg.StrOpt('loadbalancer_scheduler_driver',
               default='neutron.services.loadbalancer.agent_scheduler'
                       '.ChanceScheduler',
               help=_('Driver to use for scheduling '
                      'load balancer to a default loadbalancer agent')),
]

cfg.CONF.register_opts(AGENT_SCHEDULER_OPTS)


class DriverNotSpecified(n_exc.NeutronException):
    message = _("Device driver for agent should be specified "
                "in plugin driver.")


class LoadBalancerCallbacks(object):

    RPC_API_VERSION = '2.0'
    # history
    #   1.0 Initial version
    #   2.0 Generic API for agent based drivers
    #       - get_logical_device() handling changed;
    #       - pool_deployed() and update_status() methods added;

    def __init__(self, plugin):
        self.plugin = plugin

    def create_rpc_dispatcher(self):
        return q_rpc.PluginRpcDispatcher(
            [self, agents_db.AgentExtRpcCallback(self.plugin)])

    def get_ready_devices(self, context, host=None):
        with context.session.begin(subtransactions=True):
            agents = self.plugin.get_lbaas_agents(context,
                                                  filters={'host': [host]})
            if not agents:
                return []
            elif len(agents) > 1:
                LOG.warning(_('Multiple lbaas agents found on host %s'), host)
            pools = self.plugin.list_pools_on_lbaas_agent(context,
                                                          agents[0].id)
            pool_ids = [pool['id'] for pool in pools['pools']]

            qry = context.session.query(loadbalancer_db.Pool.id)
            qry = qry.filter(loadbalancer_db.Pool.id.in_(pool_ids))
            qry = qry.filter(
                loadbalancer_db.Pool.status.in_(
                    constants.ACTIVE_PENDING_STATUSES))
            up = True  # makes pep8 and sqlalchemy happy
            qry = qry.filter(loadbalancer_db.Pool.admin_state_up == up)
            return [id for id, in qry]

    def get_logical_device(self, context, load_balancer_id=None):
        with context.session.begin(subtransactions=True):
            qry = context.session.query(loadbalancer_db.LoadBalancer)
            qry = qry.filter_by(id=load_balancer_id)
            load_balancer = qry.one()
            retval = {}
            retval['load_balancer'] = self.plugin._make_load_balancer_dict(
                load_balancer)
            lb_dict = retval['load_balancer']
            if load_balancer.vip_port:
                lb_dict['vip_port'] = self.plugin._core_plugin._make_port_dict(
                    load_balancer.vip_port
                )
                for fixed_ip in lb_dict['vip_port']['fixed_ips']:
                    fixed_ip['subnet'] = self.plugin._core_plugin.get_subnet(
                        context, fixed_ip['subnet_id']
                    )
            # if load_balancer.listeners:
            #     retval['listeners'] = []
            #     for listener in load_balancer.listeners:
            #         listener_dict = self.plugin._make_listener_dict(listener)
            #         if listener.default_pool:
            #             listener_dict['default_pool'] = (
            #                 self.plugin._make_pool_dict(listener.default_pool))
            #         retval['listeners'].append(listener_dict)

            # if pool.vip:
            #     retval['vip'] = self.plugin._make_vip_dict(pool.vip)
            #     retval['vip']['port'] = (
            #         self.plugin._core_plugin._make_port_dict(pool.vip.port)
            #     )
            #     for fixed_ip in retval['vip']['port']['fixed_ips']:
            #         fixed_ip['subnet'] = (
            #             self.plugin._core_plugin.get_subnet(
            #                 context,
            #                 fixed_ip['subnet_id']
            #             )
            #         )
            # retval['members'] = [
            #     self.plugin._make_member_dict(m)
            #     for m in pool.members if (
            #         m.status in constants.ACTIVE_PENDING_STATUSES or
            #         m.status == constants.INACTIVE)
            # ]
            # retval['healthmonitors'] = [
            #     self.plugin._make_health_monitor_dict(hm.healthmonitor)
            #     for hm in pool.monitors
            #     if hm.status in constants.ACTIVE_PENDING_STATUSES
            # ]
            lb_dict['driver'] = (
                self.plugin.drivers[
                    load_balancer.provider.provider_name].device_driver)

            return retval

    def load_balancer_deployed(self, context, load_balancer_id):
        with context.session.begin(subtransactions=True):
            qry = context.session.query(loadbalancer_db.LoadBalancer)
            qry = qry.filter_by(id=load_balancer_id)
            lb = qry.one()

    def pool_deployed(self, context, pool_id):
        with context.session.begin(subtransactions=True):
            qry = context.session.query(loadbalancer_db.Pool)
            qry = qry.filter_by(id=pool_id)
            pool = qry.one()

            # set all resources to active
            if pool.status in constants.ACTIVE_PENDING_STATUSES:
                pool.status = constants.ACTIVE

            if (pool.vip and pool.vip.status in
                    constants.ACTIVE_PENDING_STATUSES):
                pool.vip.status = constants.ACTIVE

            for m in pool.members:
                if m.status in constants.ACTIVE_PENDING_STATUSES:
                    m.status = constants.ACTIVE

            for hm in pool.monitors:
                if hm.status in constants.ACTIVE_PENDING_STATUSES:
                    hm.status = constants.ACTIVE

    def update_status(self, context, obj_type, obj_id, status):
        model_mapping = {
            'pool': loadbalancer_db.Pool,
            'load_balancer': loadbalancer_db.LoadBalancer,
            'listener': loadbalancer_db.Listener,
            'member': loadbalancer_db.Member,
            'health_monitor': loadbalancer_db.PoolMonitorAssociation
        }
        if obj_type not in model_mapping:
            raise n_exc.Invalid(_('Unknown object type: %s') % obj_type)
        try:
            if obj_type == 'health_monitor':
                self.plugin.update_pool_health_monitor(
                    context, obj_id['monitor_id'], obj_id['pool_id'], status)
            else:
                self.plugin.update_status(
                    context, model_mapping[obj_type], obj_id, status)
        except n_exc.NotFound:
            # update_status may come from agent on an object which was
            # already deleted from db with other request
            LOG.warning(_('Cannot update status: %(obj_type)s %(obj_id)s '
                          'not found in the DB, it was probably deleted '
                          'concurrently'),
                        {'obj_type': obj_type, 'obj_id': obj_id})

    def pool_destroyed(self, context, pool_id=None):
        """Agent confirmation hook that a pool has been destroyed.

        This method exists for subclasses to change the deletion
        behavior.
        """
        pass

    def plug_vip_port(self, context, port_id=None, host=None):
        if not port_id:
            return

        try:
            port = self.plugin._core_plugin.get_port(
                context,
                port_id
            )
        except n_exc.PortNotFound:
            msg = _('Unable to find port %s to plug.')
            LOG.debug(msg, port_id)
            return

        port['admin_state_up'] = True
        port['device_owner'] = 'neutron:' + constants.LOADBALANCER
        port['device_id'] = str(uuid.uuid5(uuid.NAMESPACE_DNS, str(host)))
        port[portbindings.HOST_ID] = host
        self.plugin._core_plugin.update_port(
            context,
            port_id,
            {'port': port}
        )

    def unplug_vip_port(self, context, port_id=None, host=None):
        if not port_id:
            return

        try:
            port = self.plugin._core_plugin.get_port(
                context,
                port_id
            )
        except n_exc.PortNotFound:
            msg = _('Unable to find port %s to unplug.  This can occur when '
                    'the Vip has been deleted first.')
            LOG.debug(msg, port_id)
            return

        port['admin_state_up'] = False
        port['device_owner'] = ''
        port['device_id'] = ''

        try:
            self.plugin._core_plugin.update_port(
                context,
                port_id,
                {'port': port}
            )

        except n_exc.PortNotFound:
            msg = _('Unable to find port %s to unplug.  This can occur when '
                    'the Vip has been deleted first.')
            LOG.debug(msg, port_id)

    def update_pool_stats(self, context, pool_id=None, stats=None, host=None):
        self.plugin.update_pool_stats(context, pool_id, data=stats)


class LoadBalancerAgentApi(rpc_compat.RpcProxy):
    """Plugin side of plugin to agent RPC API."""

    BASE_RPC_API_VERSION = '2.0'
    # history
    #   1.0 Initial version
    #   1.1 Support agent_updated call
    #   2.0 Generic API for agent based drivers
    #       - modify/reload/destroy_pool methods were removed;
    #       - added methods to handle create/update/delete for every lbaas
    #       object individually;

    def __init__(self, topic):
        super(LoadBalancerAgentApi, self).__init__(
            topic, default_version=self.BASE_RPC_API_VERSION)

    def _cast(self, context, method_name, method_args, host, version=None):
        return self.cast(
            context,
            self.make_msg(method_name, **method_args),
            topic='%s.%s' % (self.topic, host),
            version=version
        )

    def create_load_balancer(self, context, lb, host, driver_name):
        return self._cast(context, 'create_load_balancer',
                          {'load_balancer': lb,
                           'driver_name': driver_name}, host)

    def update_load_balancer(self, context, old_lb, lb, host):
        return self._cast(context, 'update_load_balancer',
                          {'old_load_balancer': old_lb,
                           'load_balancer': lb}, host)

    def delete_load_balancer(self, context, lb, host):
        return self._cast(context, 'delete_load_balancer',
                          {'load_balancer': lb}, host)

    def create_listener(self, context, load_balancer_id, listener, host):
        return self._cast(context, 'create_listener',
                          {'listener': listener,
                           'load_balancer_id': load_balancer_id}, host)

    def update_listener(self, context, load_balancer_id, old_listener,
                        listener, host):
        return self._cast(context, 'update_listener',
                          {'old_listener': old_listener,
                           'listener': listener,
                           'load_balancer_id': load_balancer_id}, host)

    def delete_listener(self, context, load_balancer_id, listener, host):
        return self._cast(context, 'delete_listener',
                          {'load_balancer': listener,
                           'load_balancer_id': load_balancer_id}, host)

    def create_pool(self, context, load_balancer_id, pool, host):
        return self._cast(context, 'create_pool',
                          {'pool': pool,
                           'load_balancer_id': load_balancer_id}, host)

    def update_pool(self, context, load_balancer_id, old_pool, pool, host):
        return self._cast(context, 'update_pool',
                          {'old_pool': old_pool, 'pool': pool,
                           'load_balancer_id': load_balancer_id}, host)

    def delete_pool(self, context, load_balancer_id, pool, host):
        return self._cast(context,
                          'delete_pool',
                          {'pool': pool, 'load_balancer_id': load_balancer_id},
                          host)

    def create_member(self, context, load_balancer_id, member, host):
        return self._cast(context,
                          'create_member',
                          {'member': member,
                           'load_balancer_id': load_balancer_id}, host)

    def update_member(self, context, load_balancer_id, old_member, member,
                      host):
        return self._cast(context,
                          'update_member',
                          {'old_member': old_member, 'member': member,
                           'load_balancer_id': load_balancer_id}, host)

    def delete_member(self, context, load_balancer_id, member, host):
        return self._cast(context,
                          'delete_member',
                          {'member': member,
                           'load_balancer_id': load_balancer_id}, host)

    def create_pool_health_monitor(self, context, load_balancer_id,
                                   health_monitor, pool_id, host):
        return self._cast(context, 'create_pool_health_monitor',
                          {'health_monitor': health_monitor,
                           'pool_id': pool_id,
                           'load_balancer_id': load_balancer_id}, host)

    def update_pool_health_monitor(self, context, load_balancer_id,
                                   old_health_monitor, health_monitor,
                                   pool_id, host):
        return self._cast(context, 'update_pool_health_monitor',
                          {'old_health_monitor': old_health_monitor,
                           'health_monitor': health_monitor,
                           'pool_id': pool_id,
                           'load_balancer_id': load_balancer_id}, host)

    def delete_pool_health_monitor(self, context, load_balancer_id,
                                   health_monitor, pool_id, host):
        return self._cast(context, 'delete_pool_health_monitor',
                          {'health_monitor': health_monitor,
                           'pool_id': pool_id,
                           'load_balancer_id': load_balancer_id}, host)

    def agent_updated(self, context, admin_state_up, host):
        return self._cast(context, 'agent_updated',
                          {'payload': {'admin_state_up': admin_state_up}},
                          host)


class AgentDriverBase(abstract_driver.LoadBalancerAbstractDriver):

    # name of device driver that should be used by the agent;
    # vendor specific plugin drivers must override it;
    device_driver = None

    def __init__(self, plugin):
        if not self.device_driver:
            raise DriverNotSpecified()

        self.agent_rpc = LoadBalancerAgentApi(topics.LOADBALANCER_AGENT)

        self.plugin = plugin
        self._set_callbacks_on_plugin()
        self.plugin.agent_notifiers.update(
            {q_const.AGENT_TYPE_LOADBALANCER: self.agent_rpc})

        self.load_balancer_scheduler = importutils.import_object(
            cfg.CONF.loadbalancer_scheduler_driver)

    def _set_callbacks_on_plugin(self):
        # other agent based plugin driver might already set callbacks on plugin
        if hasattr(self.plugin, 'agent_callbacks'):
            return

        self.plugin.agent_callbacks = LoadBalancerCallbacks(self.plugin)
        self.plugin.conn = rpc.create_connection(new=True)
        self.plugin.conn.create_consumer(
            topics.LOADBALANCER_PLUGIN,
            self.plugin.agent_callbacks.create_rpc_dispatcher(),
            fanout=False)
        self.plugin.conn.consume_in_thread()

    def get_pool_agent(self, context, pool_id):
        agent = self.plugin.get_lbaas_agent_hosting_pool(context, pool_id)
        if not agent:
            raise lbaas_agentscheduler.NoActiveLbaasAgent(pool_id=pool_id)
        return agent['agent']

    def get_load_balancer_agent(self, context, load_balancer_id):
        agent = self.plugin.get_lbaas_agent_hosting_load_balancer(
            context, load_balancer_id)
        if not agent:
            raise lbaas_agentscheduler.NoActiveLbaasLoadBalancerAgent(
                load_balancer_id=load_balancer_id)
        return agent['agent']

    def create_load_balancer(self, context, load_balancer):
        agent = self.load_balancer_scheduler.schedule(self.plugin,
                                                      context, load_balancer,
                                                      self.device_driver)
        if not agent:
            raise lbaas_agentscheduler.NoEligibleLbaasLoadBalancerAgent(
                load_balancer_id=load_balancer['id'])
        self.agent_rpc.create_load_balancer(context, load_balancer,
                                            agent['host'],
                                            self.device_driver)

    def update_load_balancer(self, context, old_load_balancer, load_balancer):
        agent = self.get_load_balancer_agent(context, load_balancer['id'])
        if load_balancer['status'] in constants.ACTIVE_PENDING_STATUSES:
            self.agent_rpc.update_load_balancer(context,
                                                old_load_balancer,
                                                load_balancer,
                                                agent['host'])
        else:
            self.agent_rpc.delete_load_balancer(context, load_balancer,
                                                agent['host'])

    def delete_load_balancer(self, context, load_balancer):
        # self.plugin._delete_db_vip(context, load_balancer['id'])
        agent = self.get_load_balancer_agent(context, load_balancer['id'])
        self.agent_rpc.delete_load_balancer(context,
                                            load_balancer, agent['host'])

    def delete_listener(self, context, load_balancer_id, listener):
        agent = self.get_load_balancer_agent(context, load_balancer_id)
        self.agent_rpc.delete_listener(context, listener, agent['host'])

    def update_listener(self, context, load_balancer_id, old_listener,
                        listener):
        agent = self.get_load_balancer_agent(context, load_balancer_id)
        self.agent_rpc.update_listener(context, load_balancer_id,
                                       old_listener, listener, agent['host'])

    # def create_pool(self, context, pool):
    #     if 'loadbalancer_ids' in pool and len(pool['loadbalancer_ids']) > 0:
    #         for lb_id in pool['loadbalancer_ids']:
    #             agent = self.get_load_balancer_agent(context, lb_id)
    #             if not agent:
    #                 raise lbaas_agentscheduler.\
    #                     NoEligibleLbaasLoadBalancerAgent(lb_id=lb_id)
    #             self.agent_rpc.create_pool(context, pool, agent['host'],
    #                                        self.device_driver)
    #     #TODO: remove entire else block when old API is removed
    #     else:
    #         agent = self.pool_scheduler.schedule(self.plugin, context, pool,
    #                                              self.device_driver)
    #         if not agent:
    #             raise lbaas_agentscheduler.NoEligibleLbaasAgent(
    #                 pool_id=pool['id'])
    #         self.agent_rpc.create_pool(context, pool, agent['host'],
    #                                    self.device_driver)

    def update_pool(self, context, load_balancer_id, old_pool, pool):
        agent = self.get_load_balancer_agent(context, load_balancer_id)
        if pool['status'] in constants.ACTIVE_PENDING_STATUSES:
            self.agent_rpc.update_pool(context, old_pool, pool,
                                       agent['host'])
        else:
            self.agent_rpc.delete_pool(context, pool, agent['host'])

    def delete_pool(self, context, load_balancer_id, pool):
        agent = self.get_load_balancer_agent(context, load_balancer_id)
        self.plugin._delete_db_pool(context, pool['id'])
        self.agent_rpc.delete_pool(context, pool, agent['host'])

    def create_member(self, context, load_balancer_id, member):
        agent = self.get_load_balancer_agent(context, load_balancer_id)
        self.agent_rpc.create_member(context, member, agent['host'])

    def update_member(self, context, load_balancer_id, old_member, member):
        agent = self.get_load_balancer_agent(context, load_balancer_id)
        # member may change pool id
        if member['pool_id'] != old_member['pool_id']:
            old_pool_agent = self.plugin.get_lbaas_agent_hosting_pool(
                context, old_member['pool_id'])
            if old_pool_agent:
                self.agent_rpc.delete_member(context, old_member,
                                             old_pool_agent['agent']['host'])
            self.agent_rpc.create_member(context, member, agent['host'])
        else:
            self.agent_rpc.update_member(context, old_member, member,
                                         agent['host'])

    def delete_member(self, context, load_balancer_id, member):
        self.plugin._delete_db_member(context, member['id'])
        agent = self.get_load_balancer_agent(context, load_balancer_id)
        self.agent_rpc.delete_member(context, member, agent['host'])

    def create_pool_health_monitor(self, context, load_balancer_id, pool_id,
                                   health_monitor):
        # healthmon is not used here
        agent = self.get_load_balancer_agent(context, load_balancer_id)
        self.agent_rpc.create_pool_health_monitor(context, health_monitor,
                                                  pool_id, agent['host'])

    def update_pool_health_monitor(self, context, load_balancer_id, pool_id,
                                   old_health_monitor, health_monitor):
        agent = self.get_load_balancer_agent(context, load_balancer_id)
        self.agent_rpc.update_pool_health_monitor(context, old_health_monitor,
                                                  health_monitor, pool_id,
                                                  agent['host'])

    def delete_pool_health_monitor(self, context, load_balancer_id, pool_id,
                                   health_monitor):
        self.plugin._delete_db_pool_health_monitor(
            context, health_monitor['id'], pool_id
        )

        agent = self.get_load_balancer_agent(context, load_balancer_id)
        self.agent_rpc.delete_pool_health_monitor(context, health_monitor,
                                                  pool_id, agent['host'])

    def stats(self, context, load_balancer_id):
        pass
