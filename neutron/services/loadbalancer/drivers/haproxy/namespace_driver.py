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
from oslo.config import cfg

from neutron.common import utils as n_utils
from neutron.openstack.common import excutils
from neutron.openstack.common import importutils
from neutron.openstack.common import log as logging
from neutron.plugins.common import constants
from neutron.services.loadbalancer.agent import agent_device_driver

LOG = logging.getLogger(__name__)
NS_PREFIX = 'qlbaas-'
DRIVER_NAME = 'haproxy_ns'

STATE_PATH_DEFAULT = '$state_path/lbaas'
USER_GROUP_DEFAULT = 'nogroup'
BACKEND_DEFAULT = ('neutron.services.loadbalancer.drivers.haproxy.backends.'
                   'ns_backend.NamespaceProcessOnHostBackend')
OPTS = [
    cfg.StrOpt(
        'loadbalancer_state_path',
        default=STATE_PATH_DEFAULT,
        help=_('Location to store config and state files'),
        deprecated_opts=[cfg.DeprecatedOpt('loadbalancer_state_path')],
    ),
    cfg.StrOpt(
        'user_group',
        default=USER_GROUP_DEFAULT,
        help=_('The user group'),
        deprecated_opts=[cfg.DeprecatedOpt('user_group')],
    ),
    cfg.StrOpt(
        'backend',
        default=BACKEND_DEFAULT,
        help=_('Backend in which to install and run haproxy'),
        deprecated_opts=[cfg.DeprecatedOpt('backend')]
    )
]
cfg.CONF.register_opts(OPTS, 'haproxy')


class HaproxyNSDriver(agent_device_driver.AgentDeviceDriver):
    def __init__(self, conf, plugin_rpc):
        self.conf = conf
        self.plugin_rpc = plugin_rpc
        try:
            backend = importutils.import_object(conf.haproxy.backend, conf)
        except ImportError:
            with excutils.save_and_reraise_exception():
                msg = (_('Error importing haproxy backend: %s')
                       % conf.haproxy.backend)
                LOG.error(msg)

        self.backend = backend

    @classmethod
    def get_name(cls):
        return DRIVER_NAME

    def create(self, logical_config):
        port = logical_config['vip']['port']
        self.plugin_rpc.plug_vip_port(port['id'])
        self.backend.create(logical_config)

    def update(self, logical_config):
        self.backend.update(logical_config)

    @n_utils.synchronized('haproxy-driver')
    def undeploy_instance(self, logical_config):
        port_id = logical_config['port']['id']
        self.plugin_rpc.unplug_vip_port(port_id)
        self.backend.delete(logical_config)

    def get_stats(self, pool_id):
        logical_config = self.plugin_rpc.get_logical_device(pool_id)
        return self.backend.get_stats(logical_config)

    @n_utils.synchronized('haproxy-driver')
    def deploy_instance(self, logical_config):
        # do actual deploy only if vip and pool are configured and active
        if (not logical_config or
                'vip' not in logical_config or
                (logical_config['vip']['status'] not in
                 constants.ACTIVE_PENDING_STATUSES) or
                not logical_config['vip']['admin_state_up'] or
                (logical_config['pool']['status'] not in
                 constants.ACTIVE_PENDING_STATUSES) or
                not logical_config['pool']['admin_state_up']):
            return

        if self.backend.exists(logical_config):
            self.update(logical_config)
        else:
            self.create(logical_config)

    def _refresh_device(self, pool_id):
        logical_config = self.plugin_rpc.get_logical_device(pool_id)
        self.deploy_instance(logical_config)

    def create_vip(self, vip):
        self._refresh_device(vip['pool_id'])

    def update_vip(self, old_vip, vip):
        self._refresh_device(vip['pool_id'])

    def delete_vip(self, vip):
        logical_config = self.plugin_rpc.get_logical_device(vip['pool_id'])
        self.undeploy_instance(logical_config)

    def create_pool(self, pool):
        # nothing to do here because a pool needs a vip to be useful
        pass

    def update_pool(self, old_pool, pool):
        self._refresh_device(pool['id'])

    def delete_pool(self, pool):
        # delete_pool may be called before vip deletion in case
        # pool's admin state set to down
        logical_config = self.plugin_rpc.get_logical_device(pool['id'])
        if self.backend.exists(logical_config):
            self.undeploy_instance(logical_config)

    def create_member(self, member):
        self._refresh_device(member['pool_id'])

    def update_member(self, old_member, member):
        self._refresh_device(member['pool_id'])

    def delete_member(self, member):
        self._refresh_device(member['pool_id'])

    def create_pool_health_monitor(self, health_monitor, pool_id):
        self._refresh_device(pool_id)

    def update_pool_health_monitor(self, old_health_monitor, health_monitor,
                                   pool_id):
        self._refresh_device(pool_id)

    def delete_pool_health_monitor(self, health_monitor, pool_id):
        self._refresh_device(pool_id)
