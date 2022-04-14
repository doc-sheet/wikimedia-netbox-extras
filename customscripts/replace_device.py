from dcim.models import Device
from extras.scripts import BooleanVar, Script, StringVar


class ReplaceDevice(Script):

    class Meta:
        name = 'Move devices attributes'
        description = ('Move all attributes (cables, etc) from one device to the other. '
                       'To be used for RMAs and other in place replacement. '
                       'Note that existing attributes on the new device will be deleted.')
        commit_default = False

    source_device_name = StringVar(
        label='Old device name',
        description='Old device',
    )
    destination_device_name = StringVar(
        label='New device name',
        description='Replacement device',
    )
    already_racked = BooleanVar(
        label="Already racked",
        description=("The replacement device is already racked at its definitive location. "
                     "If checked, the script does NOT touch racking, console and power."),
    )
    move_inventory = BooleanVar(
        label="Move inventory items",
        description=("By default does NOT touch the inventory items."),
    )

    def run(self, data, commit):
        """Replace the device."""
        try:
            self._run(data)
        except Exception as e:
            self.log_failure('Failed to run script. {e}'.format(e=e))

        return self._format_logs()

    def _run(self, data):
        """Actually run the script."""
        source_device = Device.objects.get(name=data['source_device_name'])
        destination_device = Device.objects.get(name=data['destination_device_name'])

        destination_device.name = source_device.name
        source_device.name = source_device.name + '-old'
        source_device.status = 'decommissioning'
        destination_device.status = 'active'
        destination_device.comments = destination_device.comments + " - Replaced " + str(source_device.asset_tag)

        # Attributes that are set to None on the source after the swap
        attributes = ['cluster',
                      'virtual_chassis',
                      'vc_position',
                      'vc_priority',
                      'primary_ip4',
                      'primary_ip6']

        for attribute in attributes:
            source_attribute = getattr(source_device, attribute)
            destination_attribute = getattr(destination_device, attribute)
            if source_attribute == destination_attribute:
                # Ignore already set attributes to prevent excessive logging
                continue
            setattr(destination_device, attribute, source_attribute)
            self.log_success(f"[dst] Setting {attribute} to {source_attribute}")
            setattr(source_device, attribute, None)
            self.log_info(f"[src] Removing {attribute}")

        # Attributes that are kept on the source after the swap
        attributes = ['tenant']
        for attribute in attributes:
            source_attribute = getattr(source_device, attribute)
            destination_attribute = getattr(destination_device, attribute)
            if source_attribute == destination_attribute:
                # Ignore already set attributes to prevent excessive logging
                continue
            setattr(destination_device, attribute, source_attribute)
            self.log_success(f"[dst] Setting {attribute} to {source_attribute}")

        if not data['already_racked']:
            destination_device.rack = source_device.rack
            destination_device.position = source_device.position
            source_device.position = None
            destination_device.face = source_device.face
            self.log_success('[dst] Setting rack position')

        # Delete all interfaces on the destination
        for interface in destination_device.interfaces.all():
            self.log_info(f"[dst] Deleting {interface.name}.")
            interface.delete()

        # Move the interfaces from the source to the destination
        for interface in source_device.interfaces.all():
            interface.device = destination_device
            interface.save()
            self.log_success(f"Moved {interface.name}")

        if not data['already_racked']:
            # Delete all power ports on the destination
            for powerport in destination_device.powerports.all():
                self.log_info(f"[dst] Deleting {powerport.name}")
                powerport.delete()
            # Move the power ports from the source to the destination
            for powerport in source_device.powerports.all():
                powerport.device = destination_device
                powerport.save()
                self.log_success(f"Moved {powerport.name}")
            # Delete all console ports on the destination
            for consoleport in destination_device.consoleports.all():
                self.log_info(f"[dst] Deleting {consoleport.name}")
                consoleport.delete()
            # Move the console ports from the source to the destination
            for consoleport in source_device.consoleports.all():
                consoleport.device = destination_device
                consoleport.save()
                self.log_success(f"Moved {consoleport.name}")

        # In case the device is a console server
        for consoleserverport in destination_device.consoleserverports.all():
            self.log_info(f"[dst] Deleting {consoleserverport.name}")
            consoleserverport.delete()
        # Move the console server ports from the source to the destination
        for consoleserverport in source_device.consoleserverports.all():
            consoleserverport.device = destination_device
            consoleserverport.save()
            self.log_success(f"Moved {consoleserverport.name}")

        # In case the device is a PDU
        for poweroutlet in destination_device.poweroutlets.all():
            self.log_info(f"[dst] Deleting {poweroutlet.name}")
            poweroutlet.delete()
        # Move the power outlets from the source to the destination
        for poweroutlet in source_device.poweroutlets.all():
            poweroutlet.device = destination_device
            poweroutlet.save()
            self.log_success(f"Moved {poweroutlet.name}")

        if data['move_inventory']:
            # Delete all inventory items on the destination
            for inventoryitem in destination_device.inventoryitems.all():
                self.log_info(f"[dst] Deleting {inventoryitem.name}")
                inventoryitem.delete()
            # Move the inventory items from the source to the destination
            for inventoryitem in source_device.inventoryitems.all():
                inventoryitem.device = destination_device
                inventoryitem.save()
                self.log_success(f"Moved {inventoryitem.name}")

        source_device.save()
        destination_device.save()

        self.log_success(f"All done! {destination_device.asset_tag} replaced {source_device.asset_tag}")

    def _format_logs(self):
        """Return all log messages properly formatted."""
        return "\n".join(
            "[{level}] {msg}".format(level=level, msg=message) for level, message in self.log
        )
