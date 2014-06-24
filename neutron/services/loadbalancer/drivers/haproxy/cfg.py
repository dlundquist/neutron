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

import jinja2
import os
from six import moves

from neutron.agent.linux import utils
from neutron.plugins.common import constants as qconstants
from neutron.services.loadbalancer import constants


PROTOCOL_MAP = {
    constants.PROTOCOL_TCP: 'tcp',
    constants.PROTOCOL_HTTP: 'http',
    constants.PROTOCOL_HTTPS: 'tcp',
}

BALANCE_MAP = {
    constants.LB_METHOD_ROUND_ROBIN: 'roundrobin',
    constants.LB_METHOD_LEAST_CONNECTIONS: 'leastconn',
    constants.LB_METHOD_SOURCE_IP: 'source'
}

STATS_MAP = {
    constants.STATS_ACTIVE_CONNECTIONS: 'scur',
    constants.STATS_MAX_CONNECTIONS: 'smax',
    constants.STATS_CURRENT_SESSIONS: 'scur',
    constants.STATS_MAX_SESSIONS: 'smax',
    constants.STATS_TOTAL_CONNECTIONS: 'stot',
    constants.STATS_TOTAL_SESSIONS: 'stot',
    constants.STATS_IN_BYTES: 'bin',
    constants.STATS_OUT_BYTES: 'bout',
    constants.STATS_CONNECTION_ERRORS: 'econ',
    constants.STATS_RESPONSE_ERRORS: 'eresp'
}

ACTIVE_PENDING_STATUSES = qconstants.ACTIVE_PENDING_STATUSES + (qconstants.INACTIVE,)

TEMPLATE_PATH = os.path.dirname(__file__)
JINJA_ENV = None


def save_config(conf_path, logical_config, socket_path=None,
                user_group='nogroup'):
    """Convert a logical configuration to the HAProxy version."""
    template_file = os.path.join(TEMPLATE_PATH,
                                 'templates/haproxy_v1.4.template')
    template = _get_template(template_file)
    loadbalancer = [_transform_loadbalancer(logical_config,
                                            user_group='nogroup',
                                            socket_path=socket_path)]

    config_str = _render_template(template,
                                  {'loadbalancer': loadbalancer,
                                   'user_group': user_group,
                                   'stats_sock': socket_path})

    utils.replace_file(conf_path, config_str)


def _render_template(template, obj_to_render):
    return template.render(obj_to_render)


def _get_template(template_file):
    global JINJA_ENV
    if not JINJA_ENV:
        templateLoader = jinja2.FileSystemLoader(searchpath="/")
        JINJA_ENV = jinja2.Environment(loader=templateLoader)
    return JINJA_ENV.get_template(template_file)


def _transform_loadbalancer(logical_config):
    listeners = [_transform_listener(x) for x in logical_config.listeners]
    return {
        'name': logical_config.name,
        'vip_address': _get_first_ip_from_port(logical_config.vip_port),
        'listeners': listeners
    }

def _transform_listener(listener):
    pools = [_transform_pool(x) for x in listener.pools]
    return '' if listener is None else {
        'id': listener.id,
        'protocol_port': listener.protocol_port,
        'protocol': PROTOCOL_MAP[listener.protocol],
        'default_pool': _transform_pool(listener.default_pool),
        'connection_limit': listener.connection_limit,
        'x_forward_for': listener.protocol == constants.PROTOCOL_HTTP,
        'pools': pools
    }


def _transform_pool(pool):
    members = [_transform_member(x) for x in pool.members if _include_member(x)]
    health_monitor = _transform_health_monitor(pool.health_monitor)
    session_persistence = _transform_session_persistence(pool.session_persistence)
    return '' if pool is None else {
        'id': pool.id,
        'protocol': PROTOCOL_MAP[pool.protocol],
        'lb_algorithm': BALANCE_MAP.get(pool.lb_algorithm, 'roundrobin'),
        'members': members,
        'health_monitor': health_monitor,
        'session_persistence': session_persistence,
        'admin_state_up': pool.admin_state_up,
        'status': pool.status
    }


def _transform_session_persistence(persistence):
    return '' if persistence is None else {
        'type': persistence.type,
        'cookie_name': persistence.cookie_name
    }


def _transform_member(member):
    return '' if member is None else {
        'id': member.id,
        'address': member.address,
        'protocol_port': member.protocol_port,
        'weight': member.weight,
        'admin_state_up': member.admin_state_up,
        'subnet_id': member.subnet_id,
        'status': member.status
    }


def _transform_health_monitor(monitor):
    return '' if monitor is None else {
        'id': monitor.id,
        'type': monitor.type,
        'delay': monitor.delay,
        'timeout': monitor.timeout,
        'max_retries': monitor.max_retries,
        'http_method': monitor.http_method,
        'url_path': monitor.url_path,
        'expected_codes': monitor.expected_codes,
        'admin_state_up': monitor.admin_state_up,
    }


def _include_member(member):
    return member.status in ACTIVE_PENDING_STATUSES \
        and member.admin_state_up


def _get_first_ip_from_port(port):
    for fixed_ip in port.fixed_ips:
        return fixed_ip.ip_address


def _get_server_health_option(config):
    """return the first active health option."""
    for monitor in config['healthmonitors']:
        # not checking the status of healthmonitor for two reasons:
        # 1) status field is absent in HealthMonitor model
        # 2) only active HealthMonitors are fetched with
        # LoadBalancerCallbacks.get_logical_device
        if monitor['admin_state_up']:
            break
    else:
        return '', []

    server_addon = ' check inter %(delay)ds fall %(max_retries)d' % monitor
    opts = [
        'timeout check %ds' % monitor['timeout']
    ]

    if monitor['type'] in (constants.HEALTH_MONITOR_HTTP,
                           constants.HEALTH_MONITOR_HTTPS):
        opts.append('option httpchk %(http_method)s %(url_path)s' % monitor)
        opts.append(
            'http-check expect rstatus %s' %
            '|'.join(_expand_expected_codes(monitor['expected_codes']))
        )

    if monitor['type'] == constants.HEALTH_MONITOR_HTTPS:
        opts.append('option ssl-hello-chk')

    return server_addon, opts


def _get_session_persistence(config):
    persistence = config['vip'].get('session_persistence')
    if not persistence:
        return []

    opts = []
    if persistence['type'] == constants.SESSION_PERSISTENCE_SOURCE_IP:
        opts.append('stick-table type ip size 10k')
        opts.append('stick on src')
    elif (persistence['type'] == constants.SESSION_PERSISTENCE_HTTP_COOKIE and
          config.get('members')):
        opts.append('cookie SRV insert indirect nocache')
    elif (persistence['type'] == constants.SESSION_PERSISTENCE_APP_COOKIE and
          persistence.get('cookie_name')):
        opts.append('appsession %s len 56 timeout 3h' %
                    persistence['cookie_name'])

    return opts


def _has_http_cookie_persistence(config):
    return (config['vip'].get('session_persistence') and
            config['vip']['session_persistence']['type'] ==
            constants.SESSION_PERSISTENCE_HTTP_COOKIE)


def _expand_expected_codes(codes):
    """Expand the expected code string in set of codes.

    200-204 -> 200, 201, 202, 204
    200, 203 -> 200, 203
    """

    retval = set()
    for code in codes.replace(',', ' ').split(' '):
        code = code.strip()

        if not code:
            continue
        elif '-' in code:
            low, hi = code.split('-')[:2]
            retval.update(str(i) for i in moves.xrange(int(low), int(hi) + 1))
        else:
            retval.add(code)
    return retval
