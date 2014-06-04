# Copyright 2014 OpenStack Foundation
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

"""lbaas object model refactor

Revision ID: 368f983a48ae
Revises: 10cd28e692e9
Create Date: 2014-05-30 11:10:50.056524

"""

# revision identifiers, used by Alembic.
revision = '368f983a48ae'
down_revision = '10cd28e692e9'

# Change to ['*'] if this migration applies to all plugins

migration_for_plugins = [
    'neutron.services.loadbalancer.plugin.LoadBalancerPlugin',
]

from alembic import op
import sqlalchemy as sa
import sqlalchemy.sql as sasql


from neutron.db import migration


def upgrade(active_plugins=None, options=None):
    if not migration.should_run(active_plugins, migration_for_plugins):
        return

    op.create_table(
        u'loadbalancing_protocols',
        sa.Column(u'name', sa.String(255), nullable=False),
        sa.Column(u'description', sa.String(255), nullable=True),
        sa.PrimaryKeyConstraint(u'name')
    )

    lb_prots_table = sasql.table(u'loadbalancing_protocols',
                                 sa.Column(u'name', sa.String,
                                           primary_key=True,
                                           nullable=False),
                                 sa.Column(u'description',
                                           sa.String,
                                           nullable=True))

    op.bulk_insert(lb_prots_table,
                   [
                       {'name': 'HTTP', 'description': 'The HTTP Protocol'},
                       {'name': 'HTTPS', 'description': 'The HTTPS Protocol'},
                       {'name': 'TCP', 'description': 'The TCP Protocol'},
                   ])

    op.create_table(
        u'loadbalancers',
        sa.Column(u'tenant_id', sa.String(255), nullable=True),
        sa.Column(u'id', sa.String(36), nullable=False),
        sa.Column(u'name', sa.String(255), nullable=True),
        sa.Column(u'description', sa.String(255), nullable=True),
        sa.Column(u'vip_port_id', sa.String(36), nullable=True),
        sa.Column(u'vip_subnet_id', sa.String(36), nullable=False),
        sa.Column(u'connection_limit', sa.Integer(11), nullable=True),
        sa.Column(u'status', sa.String(16), nullable=False),
        sa.Column(u'admin_state_up', sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint(u'id')
    )

    op.create_table(
        u'listeners',
        sa.Column(u'tenant_id', sa.String(255), nullable=True),
        sa.Column(u'id', sa.String(36), nullable=False),
        sa.Column(u'protocol', sa.String(36), nullable=True),
        sa.Column(u'protocol_port', sa.Integer(11), nullable=True),
        sa.Column(u'default_pool_id', sa.String(36), nullable=False),
        sa.Column(u'admin_state_up', sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(['protocol'],
                                ['loadbalancing_protocols.name']),
        sa.ForeignKeyConstraint(['default_pool_id'],
                                ['pools.id']),
        sa.PrimaryKeyConstraint(u'id')
    )

    op.create_table(
        u'loadbalancerlistenerassociations',
        sa.Column(u'loadbalancer_id', sa.String(255), nullable=False),
        sa.Column(u'listener_id', sa.String(255), nullable=False),
        sa.ForeignKeyConstraint(['loadbalancer_id'], ['loadbalancers.id']),
        sa.ForeignKeyConstraint(['listener_id'], ['listeners.id']),
        sa.PrimaryKeyConstraint(u'loadbalancer_id', u'listener_id')
    )


def downgrade(active_plugins=None, options=None):
    if not migration.should_run(active_plugins, migration_for_plugins):
        return

    op.drop_table(u'loadbalancerlistenerassociations')
    op.drop_table(u'listeners')
    op.drop_table(u'loadbalancers')
    op.drop_table(u'loadbalancing_protocols')
