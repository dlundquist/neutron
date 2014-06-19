# Copyright 2013 Mirantis, Inc.
# All Rights Reserved.
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
# @author: Oleg Bondarev (obondarev@mirantis.com)
import collections

import contextlib

import mock

import os

from neutron.services.loadbalancer.drivers.haproxy import cfg
from neutron.tests import base


class TestHaproxyCfg(base.BaseTestCase):
    def test_save_config(self):
        with contextlib.nested(
                mock.patch('neutron.services.loadbalancer.'
                           'drivers.haproxy.cfg._get_template'),
                mock.patch('neutron.services.loadbalancer.'
                           'drivers.haproxy.cfg._render_template'),
                mock.patch('neutron.services.loadbalancer.'
                           'drivers.haproxy.cfg._transform_loadbalancer'),
                mock.patch('neutron.agent.linux.utils.replace_file')
        ) as (g_t, r_t, t_l, replace):
            t_l.return_value = [{'loadbalancer'}]
            r_t.return_value = 'rendered_template'

            cfg.save_config('test_path', mock.Mock())
            replace.assert_called_once_with('test_path', 'rendered_template')

    def test_render_template(self):
        template_file = os.path.join(os.path.dirname(__file__),
                                     'templates/haproxy_v1.4.template')
        rendered_obj = cfg._render_template(cfg._get_template(template_file),
                                {'loadbalancer': self.sample_ret_loadbalancer(),
                                 'user_group': 'nogroup',
                                 'stats_sock': '/sock_path'}
        )
        self.assertEqual(self.sample_expected_config(), rendered_obj)

    def test_transform_session_persistence(self):
        in_persistence = self.sample_in_session_persistence()
        ret = cfg._transform_session_persistence(in_persistence)
        self.assertEqual(self.sample_ret_session_persistence(), ret)

    def test_transform_health_monitor(self):
        in_persistence = self.sample_in_health_monitor()
        ret = cfg._transform_health_monitor(in_persistence)
        self.assertEqual(self.sample_ret_health_monitor(), ret)

    def test_transform_member(self):
        in_member = self.sample_in_member()
        ret = cfg._transform_member(in_member)
        self.assertEqual(self.sample_ret_member(), ret)

    def test_transform_pool(self):
        in_pool = self.sample_in_pool()
        ret = cfg._transform_pool(in_pool)
        self.assertEqual(self.sample_ret_pool(), ret)

    def test_transform_listener(self):
        in_listener = self.sample_in_listener()
        ret = cfg._transform_listener(in_listener)
        self.assertEqual(self.sample_ret_listener(), ret)

    def test_transform_loadbalancer(self):
        in_lb = self.sample_in_loadbalancer()
        ret = cfg._transform_loadbalancer(in_lb)
        self.assertEqual(self.sample_ret_loadbalancer(), ret)

    def sample_ret_loadbalancer(self):
        return {
            'name': 'test-lb',
            'vip_address': '10.0.0.2',
            'listeners': [self.sample_ret_listener()]
        }

    def sample_in_loadbalancer(self):
        in_lb = collections.namedtuple('loadbalancer', 'id, name,'
                                                       'vip_address,'
                                                       'vip_port,'
                                                       'listeners')
        return in_lb(
            id='sample_loadbalancer_id_1',
            name='test-lb',
            vip_address='10.0.0.2',
            vip_port=self.sample_in_vip_port(),
            listeners=[self.sample_in_listener()]
        )

    def sample_in_vip_port(self):
        vip_port = collections.namedtuple('vip_port', 'fixed_ips')
        ip_address = collections.namedtuple('ip_address', 'ip_address')
        in_address = ip_address(ip_address='10.0.0.2')
        return vip_port(fixed_ips=[in_address])

    def sample_ret_vip_port(self):
        return {
            'fixed_ips': [{
                'ip_addresses': '10.0.0.2'
                }]
        }

    def sample_ret_listener(self):
        return {
            'id': 'sample_listener_id_1',
            'protocol_port': '80',
            'protocol': 'http',
            'default_pool': self.sample_ret_pool(),
            'connection_limit': '98',
            'x_forward_for': True,
            'pools': [self.sample_ret_pool()]
        }

    def sample_in_listener(self):
        in_listener = collections.namedtuple('listener', 'id,'
                                                         'protocol_port,'
                                                         'protocol,'
                                                         'default_pool,'
                                                         'connection_limit,'
                                                         'x_forward_for,'
                                                         'pools')
        return in_listener(
            id='sample_listener_id_1',
            protocol_port='80',
            protocol='HTTP',
            default_pool=self.sample_in_pool(),
            connection_limit='98',
            x_forward_for='true',
            pools=[self.sample_in_pool()]
        )

    def sample_ret_pool(self):
        return {
            'id': 'sample_pool_id_1',
            'protocol': 'http',
            'lb_algorithm': 'roundrobin',
            'members': [self.sample_ret_member()],
            'health_monitor': self.sample_ret_health_monitor(),
            'session_persistence': self.sample_ret_session_persistence(),
            'admin_state_up': 'true',
            'status': 'ACTIVE'
        }

    def sample_in_pool(self):
        in_pool = collections.namedtuple('pool', 'id, protocol, lb_algorithm,'
                                                 'members, health_monitor,'
                                                 'session_persistence,'
                                                 'admin_state_up, status')
        return in_pool(
            id='sample_pool_id_1',
            protocol='HTTP',
            lb_algorithm='ROUND_ROBIN',
            members=[self.sample_in_member()],
            health_monitor=self.sample_in_health_monitor(),
            session_persistence=self.sample_in_session_persistence(),
            admin_state_up='true',
            status='ACTIVE')

    def sample_ret_member(self):
        return {
            'id': 'sample_member_id_1',
            'address': '10.0.0.99',
            'protocol_port': 82,
            'weight': 13,
            'subnet_id': '10.0.0.1/24',
            'admin_state_up': 'true',
            'status': 'ACTIVE'
        }

    def sample_in_member(self):
        in_member = collections.namedtuple('member',
                                           'id, address, protocol_port, '
                                           'weight, subnet_id, '
                                           'admin_state_up, status')
        return in_member(
            id='sample_member_id_1',
            address='10.0.0.99',
            protocol_port=82,
            weight=13,
            subnet_id='10.0.0.1/24',
            admin_state_up='true',
            status='ACTIVE')

    def sample_ret_session_persistence(self):
        return {
            'type': 'HTTP_COOKIE',
            'cookie_name': 'HTTP_COOKIE'
        }

    def sample_in_session_persistence(self):
        spersistence = collections.namedtuple('SessionPersistence',
                                              'type, cookie_name')
        return spersistence(type='HTTP_COOKIE',
                            cookie_name='HTTP_COOKIE')

    def sample_ret_health_monitor(self):
        return {
            'id': 'sample_monitor_id_1',
            'type': 'HTTP',
            'delay': '30',
            'timeout': '31',
            'max_retries': '3',
            'http_method': 'GET',
            'url_path': '/index.html',
            'expected_codes': '500, 405, 404',
            'admin_state_up': 'true',
        }

    def sample_in_health_monitor(self):
        monitor = collections.namedtuple('monitor', 'id, type, delay, timeout, '
                                                    'max_retries, http_method, '
                                                    'url_path, expected_codes, '
                                                    'admin_state_up')

        return monitor(id='sample_monitor_id_1', type='HTTP', delay='30',
                       timeout='31', max_retries='3', http_method='GET',
                       url_path='/index.html', expected_codes='500, 405, 404',
                       admin_state_up='true')

    def sample_expected_config(self):
        return ("# Configuration for test-lb\n"
                "global\n"
                "    daemon\n"
                "    user nobody\n"
                "    group nogroup\n"
                "    log /dev/log local0\n"
                "    log /dev/log local1 notice\n"
                "    stats socket /sock_path mode 0666 level user\n\n"
                "defaults\n"
                "    log global\n"
                "    retries 3\n"
                "    option redispatch\n"
                "    timeout connect 5000\n"
                "    timeout client 50000\n"
                "    timeout server 50000\n\n"
                "frontend sample_listener_id_1\n"
                "    option tcplog\n"
                "    maxconn 98\n"
                "    option forwardfor\n"
                "    bind 10.0.0.2:80\n"
                "    mode http\n"
                "    default_backend sample_pool_id_1\n\n"
                "backend sample_pool_id_1\n"
                "    mode http\n"
                "    balance roundrobin\n"
                "    cookie SRV insert indirect nocache\n"
                "    timeout check 31\n"
                "    server sample_member_id_1 10.0.0.99:82 weight 13 sample_member_id_1")
