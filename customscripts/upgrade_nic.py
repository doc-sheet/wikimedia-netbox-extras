from _common import Importer, format_logs, IFACE_TYPE_TO_JUNIPER_PREFIX

from dcim.choices import InterfaceTypeChoices
from dcim.models import Device, Interface
from extras.models import JournalEntry
from extras.scripts import ChoiceVar, ObjectVar, Script, StringVar


class UpgradeNic(Script, Importer):
    class Meta:
        name = "Upgrade a server to a faster interface"
        description = "Interface needs to already exists in Netbox, switch port stays the same."
        commit_default = False

    device = ObjectVar(
        model=Device,
        query_params={
            'role': 'server',
        }
    )
    new_iface = ObjectVar(
        label="Future interface",
        model=Interface,
        query_params={
            'device_id': '$device',
            'mgmt_only': False,
            'is_virtual': False,
            'cable': None
        }
    )
    cable_id = StringVar(label="Cable ID", required=False)
    interface_type_choices = (
        (InterfaceTypeChoices.TYPE_10GE_SFP_PLUS, '10G'),
        (InterfaceTypeChoices.TYPE_25GE_SFP28, '25G')
    )
    new_iface_type = ChoiceVar(label="Interface type/speed",
                               default=InterfaceTypeChoices.TYPE_10GE_SFP_PLUS,
                               choices=interface_type_choices)

    def run(self, data, commit):
        """Run the script and return all the log messages."""
        self.log_info(f"Called with parameters: {data}")
        self.upgrade_nic(data)
        return format_logs(self.log)

    def upgrade_nic(self, data):
        """Process a single device."""
        device = data['device']
        new_nbiface = data['new_iface']
        cable_id = data['cable_id']
        new_iface_type = data['new_iface_type']

        if new_nbiface.cable:
            self.log_failure(f"{device}: interface {new_nbiface} already have a cable, skipping.")
            return

        # Get the "old" server interface
        nbiface = self.find_primary_interface(device)
        if not nbiface:
            return

        if nbiface.type == new_iface_type:
            self.log_failure(f"{device}: interface {nbiface} is already {new_iface_type}, skipping.")
            return

        if not nbiface.cable:
            self.log_failure(f"{device}: interface {nbiface} doesn't have a cable, skipping.")
            return

        # Get the info needed for the new cable
        z_nbiface = self.find_remote_interface(nbiface)
        new_cable_id = cable_id if cable_id else nbiface.cable.label

        z_iface_prefix = IFACE_TYPE_TO_JUNIPER_PREFIX[z_nbiface.type]
        # delete the old cable
        nbiface.cable.delete()
        z_nbiface.refresh_from_db()
        self.log_success(f"{device}: removed cable from {nbiface}")
        # Update the server side speed as we default to 1G on unknown NICs
        if new_nbiface.type != new_iface_type:
            new_nbiface.type = new_iface_type
            self.log_success(f"{device}: updated server interface type to {new_iface_type}")
            new_nbiface.save()
        # Update the switch side speed
        if z_nbiface.type != new_iface_type:
            z_nbiface.type = new_iface_type
            self.log_success(f"{device}: updated switch interface type to {new_iface_type}")
            z_nbiface.save()

        # create the new cable
        self._create_cable(new_nbiface, z_nbiface, label=new_cable_id)

        # Experimenting with journaling
        z_nbiface_journal = JournalEntry()
        z_nbiface_journal.assigned_object = z_nbiface
        z_nbiface_journal.comments = f"Cable moved from {device}:{nbiface} to {device}:{new_nbiface}"
        z_nbiface_journal.save()

        # Update the switch side name (Juniper only)
        z_nbiface.type = new_iface_type
        old_name = z_nbiface.name
        new_prefix = IFACE_TYPE_TO_JUNIPER_PREFIX[z_nbiface.type]
        if z_iface_prefix != new_prefix:
            z_nbiface.name = old_name.replace(z_iface_prefix, new_prefix, 1)
            self.log_success(f"{device}: updated switch interface name to {z_nbiface}")
            z_nbiface.save()

        # Move the IPs to the new interface
        for ip_address in nbiface.ip_addresses.all():
            ip_address.assigned_object = new_nbiface
            ip_address.save()
            self.log_success(f"{device}: moved IP {ip_address} to {new_nbiface}")
