# vim: tabstop=4 shiftwidth=4 softtabstop=4
#
# Copyright 2014, Doug Wiegley, A10 Networks
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

from neutron.db.loadbalancer import loadbalancer_db as lb_db
from neutron.openstack.common import log as logging
from neutron.services.loadbalancer.drivers import abstract_driver

LOG = logging.getLogger(__name__)


class NoopLoadBalancerDriver(abstract_driver.LBAbstractDriver):

    def __init__(self):
        self.load_balancer = NoopLoadBalancerManager(self)
        self.listener = NoopListenerManager(self)
        self.pool = NoopPoolManager(self)
        self.member = NoopMemberManager(self)
        self.health_monitor = NoopHealthMonitorManager(self)


class NoopBaseManager(abstract_driver.BaseManager):

    def __init__(self):
        self.label = self.__class__.__name__
        self.model = None

    def create(self, context, obj):
        LOG.debug("LB %s no-op, create %s", self.label, obj.id)
        if self.model is not None:
            self.active(context, self.model, obj.id)

    def update(self, context, old_obj, obj):
        LOG.debug("LB %s no-op, update %s", self.label, obj.id)
        if self.model is not None:
            self.active(context, self.model, obj.id)

    def delete(self, context, obj):
        LOG.debug("LB %s no-op, delete %s", self.label, obj.id)


class NoopLoadBalancerManager(NoopBaseManager):

    def __init__(self):
        super(NoopLoadBalancerManager, self).__init__()
        self.model = lb_db.LoadBalancer

    def stats(self, context, lb_obj):
        LOG.debug("LB stats %s", lb_obj.id)


class NoopListenerManager(NoopBaseManager):

    def __init__(self):
        super(NoopListenerManager, self).__init__()
        self.model = lb_db.Listener


class NoopPoolManager(NoopBaseManager):

    def __init__(self):
        super(NoopPoolManager, self).__init__()
        self.model = lb_db.Pool


class NoopMemberManager(NoopBaseManager):

    def __init__(self):
        super(NoopMemberManager, self).__init__()
        self.model = lb_db.Member


class NoopHealthMonitorManager(NoopBaseManager):

    def __init__(self):
        super(NoopHealthMonitorManager, self).__init__()
        self.model = None
