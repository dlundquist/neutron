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
from neutron.common import rpc as n_rpc
from neutron.common import topics
from neutron.db import agents_db
from neutron.db.loadbalancer import loadbalancer_dbv2
from neutron.extensions import lbaas_agentscheduler
from neutron.extensions import portbindings
from neutron.openstack.common import importutils
from neutron.openstack.common import log as logging
from neutron.plugins.common import constants
from neutron.services.loadbalancer.drivers import driver_base

LOG = logging.getLogger(__name__)

AGENT_SCHEDULER_OPTS = [
    cfg.StrOpt('loadbalancer_pool_scheduler_driver',
               default='neutron.services.loadbalancer.agent_scheduler'
                       '.ChanceScheduler',
               help=_('Driver to use for scheduling '
                      'pool to a default loadbalancer agent')),
]

cfg.CONF.register_opts(AGENT_SCHEDULER_OPTS)


class DriverNotSpecified(n_exc.NeutronException):
    message = _("Device driver for agent should be specified "
                "in plugin driver.")


class LoadBalancerCallbacks(n_rpc.RpcCallback):

    RPC_API_VERSION = '3.0'
    # history
    #   1.0 Initial version
    #   2.0 Generic API for agent based drivers
    #       - get_logical_device() handling changed;
    #       - pool_deployed() and update_status() methods added;
    #   3.0 Update for LBaaS v2 object model

    def __init__(self, plugin):
        super(LoadBalancerCallbacks, self).__init__()
        self.plugin = plugin

    def get_ready_devices(self, context, host=None):
        with context.session.begin(subtransactions=True):
            agents = self.plugin.get_lbaas_agents(context,
                                                  filters={'host': [host]})
            if not agents:
                return []
            elif len(agents) > 1:
                LOG.warning(_('Multiple lbaas agents found on host %s'), host)
            load_balancers = self.plugin.list_load_balancers_on_lbaas_agent(
                                                          context,
                                                          agents[0].id)
            load_balancer_ids = [lb['id'] for lb in load_balancers['load_balancers']]

            qry = context.session.query(loadbalancer_dbv2.LoadBalancer)
            qry = qry.filter(loadbalancer_dbv2.LoadBalancer.id.in_(load_balancer_ids))
            qry = qry.filter(
                loadbalancer_dbv2.LoadBalancer.status.in_(
                    constants.ACTIVE_PENDING_STATUSES))
            up = True  # makes pep8 and sqlalchemy happy
            qry = qry.filter(loadbalancer_dbv2.LoadBalancer.admin_state_up == up)
            return [id for id, in qry]

    def load_balancer_deployed(self, context, load_balancer_id):
        with context.session.begin(subtransactions=True):
            qry = context.session.begin(loadbalancer_dbv2.LoadBalancer)
            qrt = qry.filter_by(id=load_balancer_id)
            load_balancer = qry.one()

            if load_balancer.status in constants.ACTIVE_PENDING_STATUSES:
                load_balancer.status = constants.ACTIVE

            for listener in load_balancer.listeners:
                if listener.status in constants.ACTIVE_PENDING_STATUSES:
                    listener.status = constants.ACTIVE

                if (listener.default_pool and listener.default_pool.status in
                        constants.ACTIVE_PENDING_STATUSES):
                    listener.default_pool.status = constants.ACTIVE

                # TODO(dlundquist) update members and monitors


    def load_balancer_destroyed(self, context, load_balancer_id=None):
        """Agent confirmation hook that a load balancer has been destroyed.

        This method exists for subclasses to change the deletion
        behavior.
        """
        pass


    def update_status(self, context, obj_type, obj_id, status):
        model_mapping = {
            'load_balancer': loadbalancer_dbv2.LoadBalancer,
            'listener': loadbalancer_dbv2.Listener,
            'pool': loadbalancer_dbv2.PoolV2,
            'member': loadbalancer_dbv2.MemberV2,
            'health_monitor': loadbalancer_dbv2.HealthMonitorV2
        }
        if obj_type not in model_mapping:
            raise n_exc.Invalid(_('Unknown object type: %s') % obj_type)
        try:
            plugin.update_status(
                    context, model_mapping[obj_type], obj_id, status)
        except n_exc.NotFound:
            # update_status may come from agent on an object which was
            # already deleted from db with other request
            LOG.warning(_('Cannot update status: %(obj_type)s %(obj_id)s '
                          'not found in the DB, it was probably deleted '
                          'concurrently'),
                        {'obj_type': obj_type, 'obj_id': obj_id})

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
        # TODO(change to load_balancer stats?)
        self.plugin.update_pool_stats(context, pool_id, data=stats)


class LoadBalancerAgentApi(n_rpc.RpcProxy):
    """Plugin side of plugin to agent RPC API."""

    BASE_RPC_API_VERSION = '3.0'
    # history
    #   1.0 Initial version
    #   1.1 Support agent_updated call
    #   2.0 Generic API for agent based drivers
    #       - modify/reload/destroy_pool methods were removed;
    #       - added methods to handle create/update/delete for every lbaas
    #       object individually;
    #   3.0 Generic API for v2 Object Model agent based drivers

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

    def create_load_balancer(self, context, load_balancer, host):
        return self._cast(context, 'create_load_balancer', {'load_balancer': load_balancer}, host)

    def update_load_balancer(self, context, old_load_balancer, load_balancer, host):
        return self._cast(context, 'update_load_balancer',
                          {'old_load_balancer': old_load_balancer, 'load_balancer': load_balancer}, host)

    def delete_load_balancer(self, context, load_balancer, host):
        return self._cast(context, 'delete_load_balancer', {'load_balancer': load_balancer}, host)

    def agent_updated(self, context, admin_state_up, host):
        return self._cast(context, 'agent_updated',
                          {'payload': {'admin_state_up': admin_state_up}},
                          host)


class AgentDriverBase(driver_base.LoadBalancerBaseDriver):

    # name of device driver that should be used by the agent;
    # vendor specific plugin drivers must override it;
    device_driver = None

    def __init__(self, plugin):
        if not self.device_driver:
            raise DriverNotSpecified()

        self.load_balancer = AgentLoadBalancerManager(self)
        self.listener = AgentListenerManager(self)
        self.pool = AgentPoolManager(self)
        self.member = AgentMemberManager(self)
        self.health_monitor = AgentHealthMonitorManager(self)
        self.agent_rpc = LoadBalancerAgentApi(topics.LOADBALANCER_AGENT)

        self.plugin = plugin
        self._set_callbacks_on_plugin()
        self.plugin.agent_notifiers.update(
            {q_const.AGENT_TYPE_LOADBALANCER: self.agent_rpc})

        # TODO(load balancer scheduler)
        self.pool_scheduler = importutils.import_object(
            cfg.CONF.loadbalancer_pool_scheduler_driver)

    def _set_callbacks_on_plugin(self):
        # other agent based plugin driver might already set callbacks on plugin
        if hasattr(self.plugin, 'agent_callbacks'):
            return

        self.plugin.agent_endpoints = [
            LoadBalancerCallbacks(self.plugin),
            agents_db.AgentExtRpcCallback(self.plugin)
        ]
        self.plugin.conn = n_rpc.create_connection(new=True)
        self.plugin.conn.create_consumer(
            topics.LOADBALANCER_PLUGIN,
            self.plugin.agent_endpoints,
            fanout=False)
        self.plugin.conn.consume_in_threads()

    def get_load_balancer_agent(self, context, load_balancer_id):
        agent = self.plugin.get_lbaas_agent_hosting_pool(context, load_balancer_id)
        if not agent:
            raise lbaas_agentscheduler.NoActiveLbaasAgent(load_balancer_id=load_balancer_id)
        return agent['agent']


class AgentLoadBalancerManager(driver_base.BaseListenerManager):

    def create(self, context, load_balancer):
        agent = self.driver.get_load_balancer_agent(context,
                load_balancer['id'])

        self.driver.agent_rpc.create_load_balancer(context,
                load_balancer.to_dict(listeners=True), agent['host'])

    def update(self, context, old_load_balancer, load_balancer):
        agent = self.driver.get_load_balancer_agent(context,
                old_load_balancer['id'])

        self.driver.agent_rpc.update_load_balancer(context,
                old_load_balancer.to_dict(listeners=True),
                load_balancer.to_dict(listeners=True), agent['host'])

    def delete(self, context, load_balancer):
        agent = self.driver.get_load_balancer_agent(context,
                load_balancer['id'])

        self.driver.agent_rpc.delete_load_balancer(context,
                load_balancer.to_dict(listeners=True), agent['host'])


class AgentListenerManager(driver_base.BaseListenerManager):

    def create(self, context, listener):
        pass

    def update(self, context, old_listener, listener):
        pass

    def delete(self, context, listener):
        pass


class AgentPoolManager(driver_base.BasePoolManager):

    def create(self, context, pool):
        pass

    def update(self, context, old_pool, pool):
        pass

    def delete(self, context, pool):
        pass


class AgentMemberManager(driver_base.BaseMemberManager):

    def create(self, context, obj):
        pass

    def update(self, context, obj_old, obj):
        pass

    def delete(self, context, obj):
        pass


class AgentHealthMonitorManager(driver_base.BaseHealthMonitorManager):

    def create(self, context, obj):
        pass

    def update(self, context, obj_old, obj):
        pass

    def delete(self, context, obj):
        pass

