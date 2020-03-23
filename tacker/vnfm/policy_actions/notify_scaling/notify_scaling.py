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

from oslo_log import log as logging

from tacker.plugins.common import constants
from tacker.vnfm.policy_actions import abstract_action
from tacker.vnfm import utils as vnfm_utils

LOG = logging.getLogger(__name__)


class VNFActionNotifyScaling(abstract_action.AbstractPolicyAction):
    def get_type(self):
        return 'notify-scaling'

    def get_name(self):
        return 'notify-scaling'

    def get_description(self):
        return 'Tacker VNF notify-scaling policy'

    def execute_action(self, plugin, context, vnf_dict, args):
        vnf_id = vnf_dict['id']
        vnfm_utils.log_events(context, vnf_dict,
                              constants.RES_EVT_MONITOR,
                              "ActionAutoscalingHeat invoked")
        plugin.create_vnf_scale(context, vnf_id, args)

        def start_rpc_listeners(vnf_id):
            self.endpoints = [self]
            self.connection = rpc.create_connection()
            self.connection.create_consumer(topics.TOPIC_ACTION_KILL,
                                            self.endpoints, fanout=False,
                                            host=vnf_id)
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
            except Exception:
                    LOG.exception('failed to call heat API')
                    return 'FAILED'
            return cp_dict

        old_cp_dict = get_connection_points(vnf_dict, vim_id)
        vim_id = vnf_dict['vim_id']
        attributes = vnf_dict['attributes']

        try:
            rpc.init_action_rpc(cfg.CONF)
            servers = start_rpc_listeners(vnf_id)
        except Exception:
            LOG.exception('failed to start rpc')
            return 'FAILED'
        # Call 'vnf_scaling_event' method via ConductorRPC
        try:
            target = AutoScalingRPC.AutoScalingRPC.target
            rpc_client = rpc.get_client(target)
            cctxt = rpc_client.prepare()
            # Get new_VNF status from vnfm_db
            status = cctxt.call(t_context.get_admin_context_without_session(),
                                'vnf_scaling_event', vnf_id=vnf_id)
            if status == constants.ACTIVE:
                new_cp_dict = get_connection_points(updated_vnf, vim_id)
                nfvo_plugin = manager.TackerManager.get_service_plugins()['NFVO']
                LOG.debug('old_cp_dict is %s', old_cp_dict)
                LOG.debug('new_cp_dict is %s', new_cp_dict)
                nfvo_plugin.heal_vnffg(context, vnf_dict, old_cp_dict, new_cp_dict)
        except Exception:
            LOG.exception('failed to call rpc')
            return 'FAILED'

        for server in servers:
            try:
                server.stop()
            except Exception:
                LOG.exception('failed to stop rpc connection for vnf %s',
                             new_vnf_id)
        
