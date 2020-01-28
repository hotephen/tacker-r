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
from tacker import manager

LOG = logging.getLogger(__name__)


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
        vnf_old_id = vnf_dict['id']
        LOG.info('vnf %s is dead and needs to be respawned', vnf_old_id)
        attributes = vnf_dict['attributes']
        vim_id = vnf_dict['vim_id']

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
            vnfm_utils.log_events(context, vnf_dict,
                                  constants.RES_EVT_MONITOR,
                                  "ActionRespawnHeat invoked")

        def _respawn_vnf():
            update_vnf_dict = plugin.create_vnf_sync(context, vnf_dict)
            LOG.info('respawned new vnf %s', update_vnf_dict['id'])
            plugin.config_vnf(context, update_vnf_dict)
            return update_vnf_dict

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

        #vnf_id = vnf_dict['id']
        #LOG.info('vnf id %s is dead and the event should be notified to NFVO', vnf_id)
        # def _notify_event():
        nfvo_plugin = manager.TackerManager.get_service_plugins()['NFVO']
        # To defined Check VNFFG
        #vnffg_info = nfvo_plugin.get_vnffg_referensed_vnf()
        vnf_old_id = vnf_dict['id']
        vnf_new_id = updated_vnf['id']
        nfvo_plugin.mark_event(vnf_old_id,vnf_new_id)
