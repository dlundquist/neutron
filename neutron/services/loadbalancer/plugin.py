#
# Copyright 2013 Radware LTD.
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
# @author: Avishay Balderman, Radware

from neutron.api.v2 import attributes as attrs
from neutron.common import exceptions as n_exc
from neutron import context
from neutron.db import api as qdbapi
from neutron.db.loadbalancer import loadbalancer_db as ldb
from neutron.db import servicetype_db as st_db
from neutron.extensions import loadbalancer
from neutron.openstack.common import excutils
from neutron.openstack.common import log as logging
from neutron.plugins.common import constants
from neutron.services.loadbalancer import agent_scheduler
from neutron.services import provider_configuration as pconf
from neutron.services import service_base

LOG = logging.getLogger(__name__)


class LoadBalancerPlugin(ldb.LoadBalancerPluginDb,
                         agent_scheduler.LbaasAgentSchedulerDbMixin):
    """Implementation of the Neutron Loadbalancer Service Plugin.

    This class manages the workflow of LBaaS request/response.
    Most DB related works are implemented in class
    loadbalancer_db.LoadBalancerPluginDb.
    """
    supported_extension_aliases = ["lbaas",
                                   "lbaas_agent_scheduler",
                                   "service-type"]

    # lbaas agent notifiers to handle agent update operations;
    # can be updated by plugin drivers while loading;
    # will be extracted by neutron manager when loading service plugins;
    agent_notifiers = {}

    def __init__(self):
        """Initialization for the loadbalancer service plugin."""

        qdbapi.register_models()
        self.service_type_manager = st_db.ServiceTypeManager.get_instance()
        self._load_drivers()

    def _load_drivers(self):
        """Loads plugin-drivers specified in configuration."""
        self.drivers, self.default_provider = service_base.load_drivers(
            constants.LOADBALANCER, self)

        # we're at the point when extensions are not loaded yet
        # so prevent policy from being loaded
        ctx = context.get_admin_context(load_admin_roles=False)
        # stop service in case provider was removed, but resources were not
        self._check_orphan_pool_associations(ctx, self.drivers.keys())

    def _check_orphan_pool_associations(self, context, provider_names):
        """Checks remaining associations between pools and providers.

        If admin has not undeployed resources with provider that was deleted
        from configuration, neutron service is stopped. Admin must delete
        resources prior to removing providers from configuration.
        """
        pools = self.get_pools(context)
        lost_providers = set([pool['provider'] for pool in pools
                              if pool['provider'] not in provider_names])
        # resources are left without provider - stop the service
        if lost_providers:
            msg = _("Delete associated loadbalancer pools before "
                    "removing providers %s") % list(lost_providers)
            LOG.exception(msg)
            raise SystemExit(1)

    def _get_driver_for_provider(self, provider):
        if provider in self.drivers:
            return self.drivers[provider]
        # raise if not associated (should never be reached)
        raise n_exc.Invalid(_("Error retrieving driver for provider %s") %
                            provider)

    def _get_driver_for_pool(self, context, pool_id):
        pool = self.get_pool(context, pool_id)
        try:
            return self.drivers[pool['provider']]
        except KeyError:
            raise n_exc.Invalid(_("Error retrieving provider for pool %s") %
                                pool_id)

    def get_plugin_type(self):
        return constants.LOADBALANCER

    def get_plugin_description(self):
        return "Neutron LoadBalancer Service Plugin"

    def create_load_balancer_and_listener_from_vip(self, context, vip):
        vip = vip.get('vip')
        to_lb = {'name': vip.get('name'),
                 'description': vip.get('description'),
                 'vip_subnet_id': vip.get('subnet_id'),
                 'connection_limit': vip.get('connection_limit'),
                 'admin_state_up': vip.get('admin_state_up')}
        to_lb = {'load_balancer': to_lb}

        to_listener = {'protocol': vip.get('protocol'),
                       'protocol_port': vip.get('protocol_port'),
                       'default_pool_id': vip.get('pool_id'),
                       'admin_state_up': vip.get('admin_state_up')}
        to_listener = {'listener': to_listener}
        lb_dict = super(LoadBalancerPlugin,
                        self).create_load_balancer_and_listener(context,
                                                                to_lb,
                                                                to_listener)
        return lb_dict

    def _load_balancer_to_vip(self, lb):
        vip = {'tenant_id': lb.get('tenant_id'),
               'id': lb.get('id'),
               'name': lb.get('name'),
               'description': lb.get('description'),
               'port_id': lb.get('vip_port_id'),
               'status': lb.get('status'),
               'admin_state_up': lb.get('admin_state_up'),
               'connection_limit': lb.get('connection_limit'),
               'status_description': ''}
        if lb.get('listeners') and len(lb['listeners']) > 0:
            listener = lb['listeners'][0]
            vip['protocol_port'] = listener.get('protocol_port')
            vip['protocol'] = listener.get('protocol')
            vip['pool_id'] = listener.get('default_pool_id')
            vip['admin_state_up'] = (lb.get('admin_state_up') and
                                     listener.get('admin_state_up'))
        return vip

    def create_vip(self, context, vip):
        lb = self.create_load_balancer_and_listener_from_vip(context, vip)
        pool = lb.listeners[0].default_pool

        self.service_type_manager.add_resource_association(
            context,
            constants.LOADBALANCER,
            provider_name, p['id'])
        # driver = self._get_driver_for_pool(context, v['pool_id'])
        # driver.create_vip(context, v)
        return self._load_balancer_to_vip(lb_db)

    def get_vip(self, context, id_, fields=None):
        lb_dict = super(LoadBalancerPlugin, self).get_load_balancer(
            context, id_, fields=None)
        return self._load_balancer_to_vip(lb_dict)

    def get_vips(self, context, filters=None, fields=None):
        lb_list = super(LoadBalancerPlugin, self).get_load_balancers(
            context, filters=filters, fields=fields)
        ret = [self._load_balancer_to_vip(lb) for lb in lb_list]
        return ret

    def update_vip(self, context, id, vip):
        if 'status' not in vip['vip']:
            vip['vip']['status'] = constants.PENDING_UPDATE
        old_vip = self.get_vip(context, id)
        v = super(LoadBalancerPlugin, self).update_vip(context, id, vip)
        driver = self._get_driver_for_pool(context, v['pool_id'])
        driver.update_vip(context, old_vip, v)
        return v

    def _delete_db_vip(self, context, id):
        # proxy the call until plugin inherits from DBPlugin
        super(LoadBalancerPlugin, self).delete_vip(context, id)

    def delete_vip(self, context, id):
        self.update_status(context, ldb.Vip,
                           id, constants.PENDING_DELETE)
        v = self.get_vip(context, id)
        driver = self._get_driver_for_pool(context, v['pool_id'])
        driver.delete_vip(context, v)

    def _get_provider_name(self, context, entity):
        if ('provider' in entity and
            entity['provider'] != attrs.ATTR_NOT_SPECIFIED):
            provider_name = pconf.normalize_provider_name(entity['provider'])
            self.validate_provider(provider_name)
            return provider_name
        else:
            if not self.default_provider:
                raise pconf.DefaultServiceProviderNotFound(
                    service_type=constants.LOADBALANCER)
            return self.default_provider

    def create_pool(self, context, pool):
        provider_name = self._get_provider_name(context, pool['pool'])
        p = super(LoadBalancerPlugin, self).create_pool(context, pool)

        #TODO: remove once old API has been removed, provider will be
        #associated with load balancer in new API
        self.service_type_manager.add_resource_association(
            context,
            constants.LOADBALANCER,
            provider_name, p['id'])
        #need to add provider name to pool dict,
        #because provider was not known to db plugin at pool creation
        p['provider'] = provider_name
        driver = self.drivers[provider_name]
        try:
            driver.create_pool(context, p)
        except loadbalancer.NoEligibleBackend:
        #     that should catch cases when backend of any kind
        #     is not available (agent, appliance, etc)
            self.update_status(context, ldb.Pool,
                               p['id'], constants.ERROR,
                               "No eligible backend")
            raise loadbalancer.NoEligibleBackend(pool_id=p['id'])
        return p

    def update_pool(self, context, id, pool):
        if 'status' not in pool['pool']:
            pool['pool']['status'] = constants.PENDING_UPDATE
        old_pool = self.get_pool(context, id)
        p = super(LoadBalancerPlugin, self).update_pool(context, id, pool)
        driver = self._get_driver_for_provider(p['provider'])
        driver.update_pool(context, old_pool, p)
        return p

    def _delete_db_pool(self, context, id):
        # proxy the call until plugin inherits from DBPlugin
        # rely on uuid uniqueness:
        try:
            with context.session.begin(subtransactions=True):
                self.service_type_manager.del_resource_associations(
                    context, [id])
                super(LoadBalancerPlugin, self).delete_pool(context, id)
        except Exception:
            # that should not happen
            # if it's still a case - something goes wrong
            # log the error and mark the pool as ERROR
            LOG.error(_('Failed to delete pool %s, putting it in ERROR state'),
                      id)
            with excutils.save_and_reraise_exception():
                self.update_status(context, ldb.Pool,
                                   id, constants.ERROR)

    def delete_pool(self, context, id):
        # check for delete conditions and update the status
        # within a transaction to avoid a race
        with context.session.begin(subtransactions=True):
            self.update_status(context, ldb.Pool,
                               id, constants.PENDING_DELETE)
            self._ensure_pool_delete_conditions(context, id)
        p = self.get_pool(context, id)
        driver = self._get_driver_for_provider(p['provider'])
        driver.delete_pool(context, p)

    def create_member(self, context, member):
        m = super(LoadBalancerPlugin, self).create_member(context, member)
        driver = self._get_driver_for_pool(context, m['pool_id'])
        driver.create_member(context, m)
        return m

    def update_member(self, context, id, member):
        if 'status' not in member['member']:
            member['member']['status'] = constants.PENDING_UPDATE
        old_member = self.get_member(context, id)
        m = super(LoadBalancerPlugin, self).update_member(context, id, member)
        driver = self._get_driver_for_pool(context, m['pool_id'])
        driver.update_member(context, old_member, m)
        return m

    def _delete_db_member(self, context, id):
        # proxy the call until plugin inherits from DBPlugin
        super(LoadBalancerPlugin, self).delete_member(context, id)

    def delete_member(self, context, id):
        self.update_status(context, ldb.Member,
                           id, constants.PENDING_DELETE)
        m = self.get_member(context, id)
        driver = self._get_driver_for_pool(context, m['pool_id'])
        driver.delete_member(context, m)

    def _validate_hm_parameters(self, delay, timeout):
        if delay < timeout:
            raise loadbalancer.DelayOrTimeoutInvalid()

    def create_health_monitor(self, context, health_monitor):
        new_hm = health_monitor['health_monitor']
        self._validate_hm_parameters(new_hm['delay'], new_hm['timeout'])

        hm = super(LoadBalancerPlugin, self).create_health_monitor(
            context,
            health_monitor
        )
        return hm

    def update_health_monitor(self, context, id, health_monitor):
        new_hm = health_monitor['health_monitor']
        old_hm = self.get_health_monitor(context, id)
        delay = new_hm.get('delay', old_hm.get('delay'))
        timeout = new_hm.get('timeout', old_hm.get('timeout'))
        self._validate_hm_parameters(delay, timeout)

        hm = super(LoadBalancerPlugin, self).update_health_monitor(
            context,
            id,
            health_monitor
        )

        with context.session.begin(subtransactions=True):
            qry = context.session.query(
                ldb.PoolMonitorAssociation
            ).filter_by(monitor_id=hm['id']).join(ldb.Pool)
            for assoc in qry:
                driver = self._get_driver_for_pool(context, assoc['pool_id'])
                driver.update_pool_health_monitor(context, old_hm,
                                                  hm, assoc['pool_id'])
        return hm

    def _delete_db_pool_health_monitor(self, context, hm_id, pool_id):
        super(LoadBalancerPlugin, self).delete_pool_health_monitor(context,
                                                                   hm_id,
                                                                   pool_id)

    def _delete_db_health_monitor(self, context, id):
        super(LoadBalancerPlugin, self).delete_health_monitor(context, id)

    def create_pool_health_monitor(self, context, health_monitor, pool_id):
        retval = super(LoadBalancerPlugin, self).create_pool_health_monitor(
            context,
            health_monitor,
            pool_id
        )
        monitor_id = health_monitor['health_monitor']['id']
        hm = self.get_health_monitor(context, monitor_id)
        driver = self._get_driver_for_pool(context, pool_id)
        driver.create_pool_health_monitor(context, hm, pool_id)
        return retval

    def delete_pool_health_monitor(self, context, id, pool_id):
        self.update_pool_health_monitor(context, id, pool_id,
                                        constants.PENDING_DELETE)
        hm = self.get_health_monitor(context, id)
        driver = self._get_driver_for_pool(context, pool_id)
        driver.delete_pool_health_monitor(context, hm, pool_id)

    def stats(self, context, pool_id):
        driver = self._get_driver_for_pool(context, pool_id)
        stats_data = driver.stats(context, pool_id)
        # if we get something from the driver -
        # update the db and return the value from db
        # else - return what we have in db
        if stats_data:
            super(LoadBalancerPlugin, self).update_pool_stats(
                context,
                pool_id,
                stats_data
            )
        return super(LoadBalancerPlugin, self).stats(context,
                                                     pool_id)

    def populate_vip_graph(self, context, vip):
        """Populate the vip with: pool, members, healthmonitors."""

        pool = self.get_pool(context, vip['pool_id'])
        vip['pool'] = pool
        vip['members'] = [self.get_member(context, member_id)
                          for member_id in pool['members']]
        vip['health_monitors'] = [self.get_health_monitor(context, hm_id)
                                  for hm_id in pool['health_monitors']]
        return vip

    def validate_provider(self, provider):
        if provider not in self.drivers:
            raise pconf.ServiceProviderNotFound(
                provider=provider, service_type=constants.LOADBALANCER)
