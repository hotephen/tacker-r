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
from oslo_config import cfg
from oslo_log import log as logging
from oslo_utils import timeutils

from tacker.db.common_services import common_services_db_plugin
from tacker.plugins.common import constants
from tacker.vnfm.infra_drivers.openstack import heat_client as hc
from tacker.vnfm.policy_actions import abstract_action
from tacker.vnfm import utils as vnfm_utils
from tacker.vnfm import vim_client
from tacker import manager
from tacker.common import rpc
from tacker.common import topics
from tacker.conductor.conductorrpc import AutoHealingRPC
from tacker import context as t_context


LOG = logging.getLogger(__name__)


def _log_monitor_events(context, vnf_dict, evt_details):
    _cos_db_plg = common_services_db_plugin.CommonServicesPluginDb()
    _cos_db_plg.create_event(context, res_id=vnf_dict['id'],
                             res_type=constants.RES_TYPE_VNF,
                             res_state=vnf_dict['status'],
                             evt_type=constants.RES_EVT_MONITOR,
                             tstamp=timeutils.utcnow(),
                             details=evt_details)

class VNFActionNotify(abstract_action.AbstractPolicyAction):
    def get_type(self):
        return 'notify'

    def get_name(self):
        return 'notify'

    def get_description(self):
        return 'Tacker VNF notify policy'

    # Should define the below action
    # When event ocurrs, VNFM only notify event with related infromation  (e.g., vnf_id) to NFVO
    # Then, NFVO finds VNFFG which include the VNF and decides wether VNFFG should be deleted or changed.
    def execute_action(self, plugin, context, vnf_dict, args):
        # Respawn Action
        vnf_id = vnf_dict['id']
        vim_id = vnf_dict['vim_id']
        attributes = vnf_dict['attributes']
        old_cp_dict = get_connection_points(vnf_dict, vim_id)
        LOG.info('vnf %s is dead and needs to be respawned', vnf_id)

        def _update_failure_count():
            failure_count = int(attributes.get('failure_count', '0')) + 1
            failure_count_str = str(failure_count)
            LOG.debug("vnf %(vnf_id)s failure count %(failure_count)s",
                      {'vnf_id': vnf_id, 'failure_count': failure_count_str})
            attributes['failure_count'] = failure_count_str
            attributes['dead_instance_id_' + failure_count_str] = vnf_dict[
                'instance_id']

        def _fetch_vim(vim_uuid):
            vim_res = vim_client.VimClient().get_vim(context, vim_uuid)
            return vim_res

        def _delete_heat_stack(vim_auth):
            placement_attr = vnf_dict.get('placement_attr', {})
            region_name = placement_attr.get('region_name')
            heatclient = hc.HeatClient(auth_attr=vim_auth,
                                       region_name=region_name)
            heatclient.delete(vnf_dict['instance_id'])
            LOG.debug("Heat stack %s delete initiated",
                      vnf_dict['instance_id'])
            _log_monitor_events(context, vnf_dict, "ActionRespawnHeat invoked")

        def _respawn_vnf():
            update_vnf_dict = plugin.create_vnf_sync(context, vnf_dict)
            LOG.info('respawned new vnf %s', update_vnf_dict['id'])
            plugin.config_vnf(context, update_vnf_dict)
            return update_vnf_dict


        def start_rpc_listeners(vnf_id):
            self.endpoints = [self]
            self.connection = rpc.create_connection()
            self.connection.create_consumer(topics.TOPIC_ACTION_KILL,
                                            self.endpoints, fanout=False,
                                            host=vnf_id)
            #LOG.info('log: self.endpoints = %s', self.endpoints) ###
            #LOG.info('log: self.connection = %s', self.connection) ###
            #LOG.info('log: create_consumer completed') ###
            return self.connection.consume_in_threads()
        
        def get_connection_points(vnf_dict, vim_id):
            instance_id = vnf_dict['instance_id']
            placement_attr = vnf_dict.get('placement_attr', {})
            region_name = placement_attr.get('region_name')
            vim_res = _fetch_vim(vim_id)
            try:
                heatclient = hc.HeatClient(auth_attr=vim_res['vim_auth'],
                                            region_name=region_name)
                resources_ids = heatclient.resource_get_list(instance_id, 
                                            nested_depth=2)
                vnf_details = {resource.resource_name:
                        {"id": resource.physical_resource_id,
                         "type": resource.resource_type}
                        for resource in resources_ids}
                cp_dict = {}
                for name, info in vnf_details.items():
                    if info.get('type') == 'OS::Neutron::Port':
                        cp_dict[name] = info.get('id')
                LOG.info('log: cp_dict : %s', cp_dict) ###
            except Exception:
                    LOG.exception('failed to call heat API')
                    return 'FAILED'
            return cp_dict


        # 1.Respawn Action
        if plugin._mark_vnf_dead(vnf_dict['id']):
            _update_failure_count()
            vim_res = _fetch_vim(vim_id)
            if vnf_dict['attributes'].get('monitoring_policy'):
                plugin._vnf_monitor.mark_dead(vnf_dict['id'])
                _delete_heat_stack(vim_res['vim_auth'])
                updated_vnf = _respawn_vnf()
                plugin.add_vnf_to_monitor(context, updated_vnf)
                LOG.debug("VNF %s added to monitor thread",
                          updated_vnf['id'])
            if vnf_dict['attributes'].get('alarming_policy'):
                _delete_heat_stack(vim_res['vim_auth'])
                vnf_dict['attributes'].pop('alarming_policy')
                _respawn_vnf()        
        

        # 2. Notify and Healing Action        
        new_vnf_id = updated_vnf['id']
        LOG.info('log : new_vnf %s is respawned and needs to notify', new_vnf_id)
        
        # 2.1 Start rpc connection
        try:
            rpc.init_action_rpc(cfg.CONF) ###
            servers = start_rpc_listeners(new_vnf_id)
        except Exception:
            LOG.exception('failed to start rpc')
            return 'FAILED'

        # 2.2 Call 'vnf_respawning_event' method via ConductorRPC
        try:
            target = AutoHealingRPC.AutoHealingRPC.target
            rpc_client = rpc.get_client(target)
            cctxt = rpc_client.prepare()

            # Get new_VNF status from VNF_DB
            status = cctxt.call(t_context.get_admin_context_without_session(),
                                'vnf_respawning_event', vnf_id=new_vnf_id)
            #LOG.info('log: new_vnf status = %s', status) ###
            if status == constants.ACTIVE:
                # Get new_VNF CP from Heat API
                new_cp_dict = get_connection_points(updated_vnf, vim_id)
                # Call vnffg-healing function
                nfvo_plugin = manager.TackerManager.get_service_plugins()['NFVO']
                LOG.info('NFVO_plugin is called')
                LOG.info('old_cp_dict is %s', old_cp_dict)
                LOG.info('new_cp_dict is %s', new_cp_dict)

    #           nfvo_plugin.mark_event(context, vnf_id, old_cp_dict, new_cp_dict)
    

        except Exception:
            LOG.exception('failed to call rpc')
            return 'FAILED'

        # 2.3 Stop rpc connection
        for server in servers:
            try:
                server.stop()
            except Exception:
                LOG.exception('failed to stop rpc connection for vnf %s',
                             new_vnf_id)