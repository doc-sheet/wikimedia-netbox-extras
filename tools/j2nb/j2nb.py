#!/usr/bin/env python3


# Nice to implement:
# - VRRP VIP (low priority)
# - Circuits based on interface description
# - interface mode/vlans

import argparse
import ipaddress
import logging
import sys
import yaml

from contextlib import contextmanager

import pynetbox
from jnpr.junos import Device
from jnpr.junos.exception import RpcError
from jnpr.junos.factory import loadyaml
from jnpr.junos.op.lacp import LacpPortTable
from jnpr.junos.op.xcvr import XcvrTable
from jnpr.junos.op.vlan import VlanTable

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger()
logging.getLogger("ncclient.transport").setLevel(logging.WARNING)
logging.getLogger("ncclient.operations.rpc").setLevel(logging.WARNING)

interfaces_whitelist = ['et-', 'xe-', 'ge-', 'ae', 'fxp', 'gr-', 'lo0', 'vme', 'reth', 'st', 'me0', 'em0', 'vlan']
interfaces_blacklist = ['.32767', '.32768', '.16386', '.16385', '.16384', 'fxp2', 'bme']
mgmt_interfaces_names = ['me0', 'em0', 'em1', 'vme', 'fxp0']
loopback_interfaces_names = ['lo0']
master_interfaces_names = ['vme', 'fxp0', 'ae', 'reth', 'lo0', 'st0', 'me0', 'em0', 'gr-']


class NetboxDevice():
    def __init__(self, name, config):
        logger.info('Connecting to Netbox')
        self.netbox = pynetbox.api(config['url'], token=config['api_key'])
        self.name = name
        self._vc_master = None  # Can be None/False/<device object> item
        self._device = None
        self._vc_members = None
        self._vlans = None

    @property
    def device(self):
        if not self._device:
            device = self.netbox.dcim.devices.get(name=self.name)
            if device:
                self._device = device
                return device
            elif self.virtual_chassis_master:
                self._device = self.virtual_chassis_master
                return self.virtual_chassis_master
            else:
                logger.error(self.name + ' Not in Netbox.')
                sys.exit(1)
        return self._device

    @property
    def virtual_chassis_master(self):
        if self._vc_master is None:
            self._vc_master = False
            virtual_chassis = self.netbox.dcim.virtual_chassis.all()
            logger.debug('Looking for VC master named :' + self.name)
            if virtual_chassis:
                for vc in virtual_chassis:
                    if vc.domain == self.name:
                        logger.info('VC master is : ' + vc.master.name)
                        self._vc_master = vc.master
            logger.debug('No VC master named: ' + self.name)
        return self._vc_master

    @property
    def virtual_chassis_members(self):
        if not self._vc_members:
            virtual_chassis = self.netbox.dcim.virtual_chassis.all()
            logger.info('Looking for VC members')
            for vc in virtual_chassis:
                if vc.domain == self.name:
                    vc_id = vc.id
                    vc_members = self.netbox.dcim.devices.filter(virtual_chassis_id=vc_id)
                    self._vc_members = vc_members
        return self._vc_members

    @property
    def interfaces(self):
        try:
            logger.debug('Pull interfaces from Netbox')
            interfaces = self.netbox.dcim.interfaces.filter(device_id=self.device.id)
        except pynetbox.RequestError as e:
            logger.error('Error getting device interfaces - ' + e.error)
            sys.exit(1)

        interfaces_serial = []
        # Itterate over all the intefaces and convert them to a list of dicts
        for interface in interfaces:
            interfaces_serial.append(interface.serialize())
        return interfaces_serial

    @property
    def ips(self):
        try:
            logger.debug('Pull interfaces from Netbox')
            ips = self.netbox.ipam.ip_addresses.filter(device_id=self.device.id)
        except pynetbox.RequestError as e:
            logger.error('Error getting device IPs - ' + e.error)
            sys.exit(1)

        ips_serial = []
        # Itterate over all the intefaces and convert them to a list of dicts
        for ip in ips:
            ips_serial.append(ip.serialize())
        return ips_serial

    def get_interface_id(self, interface_name):
        try:
            interface = self.netbox.dcim.interfaces.get(device_id=self.device.id, name=interface_name)
            if interface:
                return interface.id
        except pynetbox.RequestError as e:
            logger.error('Error getting interface ' + interface_name + ' - ' + e.error)

    def get_vlan_id(self, vlan_tag):
        if not self._vlans:
            logger.info('Getting all vlans from Netbox')
            self._vlans = self.netbox.ipam.vlans.filter(site_id=self.device.site.id)
        for vlan in self._vlans:
            if vlan.vid == vlan_tag:
                return vlan.id

    def compare_dicts(self, outdated, goal):
        d1_keys = outdated.keys()
        d2_keys = goal.keys()
        intersect_keys = set(d1_keys).intersection(set(d2_keys))
        modified = {}
        for i in intersect_keys:
            if type(outdated[i]) is list:
                outdated[i].sort()
                goal[i].sort()
            if outdated[i] != goal[i]:
                modified.update({i: (outdated[i], goal[i])})

        return modified

    def create_ips(self, ips):
        ips_to_update = []
        for ip in ips:
            try:
                self.netbox.ipam.ip_addresses.create(ip)
                logger.info("Created " + ip['address'] + ' in Netbox')
            except pynetbox.RequestError as e:
                logger.error('Error creating ' + ip['address'] + ' - ' + e.error)
                # TODO: only add to ips_to_update if the error is something like "already exists"
                ips_to_update.append(ip)
        # TODO risk of race between create (update) and delete
        # Eg. an IP is moved from one device to another, if the source device is being processed after the destination one
        if ips_to_update:
            self.update_ips(ips_to_update)

    def update_ips(self, ips):
        for ip in ips:
            try:
                ip_to_update = self.netbox.ipam.ip_addresses.get(address=ip['address'])
                ip_to_update.update(ip)
                logger.info("Updated " + ip['address'] + ' in Netbox')
            except pynetbox.RequestError as e:
                logger.error('Error updating ' + ip['address'] + ' - ' + e.error)

    def delete_ips(self, ips):
        for ip in ips:
            try:
                ip_to_delete = self.netbox.ipam.ip_addresses.get(address=ip['address'])
                ip_to_delete.delete()
                logger.info("Deleted " + ip['address'] + ' from Netbox')
            except pynetbox.RequestError as e:
                logger.error('Error deleting ' + ip['address'] + ' - ' + e.error)

    def create_interfaces(self, interfaces):
        for interface in interfaces:
            if 'lag' in interface and interface['lag'] and not isinstance(interface['lag'], int):
                parent_interface_id = self.get_interface_id(interface['lag'])
                if parent_interface_id:
                    interface['lag'] = parent_interface_id
                else:
                    logger.error(interface['lag'] + ' (parent) interface not found, maybe next run?')
                    sys.exit(1)
            try:
                self.netbox.dcim.interfaces.create(interface)
                logger.info("Created " + interface['name'] + ' in Netbox')
            except pynetbox.RequestError as e:
                logger.error('Error creating ' + interface['name'] + ' - ' + e.error)

    def update_interfaces(self, interfaces):
        for interface in interfaces:
            try:
                interface_to_update = self.netbox.dcim.interfaces.get(self.get_interface_id(interface['name']))
                interface_to_update.update(interface)
                logger.info("Updated " + interface['name'] + ' in Netbox')
            except pynetbox.RequestError as e:
                logger.error('Error updating ' + interface['name'] + ' - ' + e.error)

    def delete_interfaces(self, interfaces):
        for interface in interfaces:
            try:
                netbox_interface = self.netbox.dcim.interfaces.get(device_id=interface['device'],
                                                                   name=interface['name'])
                if netbox_interface:
                    netbox_interface.delete()
                    logger.info("Deleted " + interface['name'] + ' from Netbox')
                else:
                    logger.error('Error finding interface ' + interface['name'])
            except pynetbox.RequestError as e:
                logger.error('Error deleting ' + interface['name'] + ' - ' + e.error)


class JuniperDevice():
    @contextmanager
    def connect(self):
        logger.debug('Connecting to device with Junos PYEZ')
        self.device.open()
        try:
            yield
        finally:
            self.device.close()

    def __init__(self, fqdn, config):
        if 'username' in config:
            username = config['username']
        else:
            username = ''
        self.device = Device(fqdn, username, '', port=22)

        self._interfaces = {}
        self._lacp_associations = {}
        self._xcvrs = {}
        self._ips = {}

    @property
    def xcvrs(self):
        if self._xcvrs is False:
            return {}
        elif not self._xcvrs:
            with self.connect():
                xcvrs_name_type = {}
                logger.info('Get transceivers')
                xcvrs = XcvrTable(self.device).get()
                if not xcvrs:
                    logger.info('No transceivers')
                    self._xcvrs = False
                    return {}
                for (f, p, x), values in xcvrs.items():
                    interface = f.split(' ')[1] + '/' + p.split(' ')[1] + '/' + x.split(' ')[1]
                    type = values[3][1]
                    xcvrs_name_type[interface] = type
                self._xcvrs = xcvrs_name_type
        return self._xcvrs

    def get_parent(self, interface_name):
        # If _lacp_associations is None, tries to get it
        # it _lacp_associations is False, LACP not running, don't try it Aggregation
        # Otherwise return the list of associations
        if self._lacp_associations is False:
            return None
        elif not self._lacp_associations:
            try:
                with self.connect():
                    logger.info('Get Juniper LACP status')
                    lacpPorts = LacpPortTable(self.device).get()
                    lacp_parent_child = {}
                    for lacpParent in lacpPorts:
                        for lacpChild in lacpParent.proto:
                            lacp_parent_child[lacpChild.name] = lacpParent.name
                    self._lacp_associations = lacp_parent_child
            except RpcError:
                logger.info('No LACP running - ignoring')
                self._lacp_associations = False
                return None

        if interface_name in self._lacp_associations:
            return self._lacp_associations[interface_name]

    # Interface name to  Netbox interface type, fist match wins
    # From https://github.com/digitalocean/netbox/blob/ac363394913e76f135d68dca3d509762a7d092d0/netbox/dcim/constants.py
    # interface_name (string): Juniper formated interface name
    # return (int): Netbox form factor ID
    def intf_name_to_form_factor(self, interface_name):
        iftype_to_code = {'SFP-T': 1100,  # SFP (1GE)
                          'SFP-LX10': 1100,  # SFP (1GE)
                          'SFP+-10G-LR': 1200,  # SFP+ (10GE)
                          'SFP+-10G-CU3M': 1200,  # SFP+ (10GE)
                          'SFP+-10G-CU5M': 1200,  # SFP+ (10GE)
                          'QSFP+40GE-LX4': 1400,  # QSFP+ (40GE)
                          'QSFP+-40G-SR4': 1400,  # QSFP+ (40GE)
                          }
        ifname_to_code = {'.': 0,  # Virtual Everything with a dot is a subinterface
                          'lo': 0,  # Virtual
                          'gr': 0,  # Virtual
                          'vme': 0,  # Virtual
                          'reth': 0,  # Virtual
                          'st': 0,  # Virtual
                          'vlan': 0,  # Virtual
                          'et-': 1400,  # QSFP+ (40GE)
                          'xe-': 1200,  # SFP+ (10GE)
                          'ae': 200,  # Link Aggregation Group (LAG)
                          'fxp': 1000,  # 1000BASE-T (1GE)
                          'em0': 1000,  # 1000BASE-T (1GE)
                          'me0': 1000,  # 1000BASE-T (1GE)
                          'ge-': 1000,  # 1000BASE-T (1GE)
                          }

        if '-' in interface_name and 'gr' not in interface_name:
            (prefix, suffix) = interface_name.split('-')
            if suffix in self.xcvrs:
                for k, v in iftype_to_code.items():
                    if k in self.xcvrs[suffix]:
                        return v
                logger.error(self.xcvrs[suffix] + ' With no set code.')
                sys.exit(1)

        for k, v in ifname_to_code.items():
            if k in interface_name:
                return v

    def _vlans(self):
        logger.info('Get Juniper Vlans')
        globals().update(loadyaml('vlans.yml'))
        try:
            vlans = VlanTable(self.device).get()
        except RpcError:
            logger.info('No vlans - ignoring')
            return {}
        interfaces = {}
        for vlan in vlans:
            vlanid = int(vlan.tag)
            for member in vlan.members:
                interface_name = member.interface.replace("*", "")
                if interface_name not in interfaces:
                    interfaces[interface_name] = {}
                    if member.tagness == 'tagged':
                        # Access: One untagged VLAN (100)
                        # Tagged: One untagged VLAN and/or one or more tagged VLANs (200)
                        interfaces[interface_name]['mode'] = 200
                        interfaces[interface_name]['tagged_vlans'] = [vlanid]
                    else:
                        interfaces[interface_name]['mode'] = 100
                        interfaces[interface_name]['untagged_vlan'] = vlanid
                        interfaces[interface_name]['tagged_vlans'] = []
                else:  # if the interface has more than 1 vlan
                    if member.tagness == 'tagged':
                        # Ensure the mode will always be tagged if there is at least 1 tagged interface
                        interfaces[interface_name]['mode'] = 200
                        interfaces[interface_name]['tagged_vlans'].append(vlanid)
                    else:
                        interfaces[interface_name]['untagged_vlan'] = vlanid
        return interfaces

    # Juniper reports all its interfaces as vcp-255/x/y, I changed that for clarity to vcp-z/x/y,
    # Where z is the vcp ID, so it's easier to understand, and j2nb automatically assign it to the proper device
    def _vcp(self):
        logger.info('Get Juniper VCP')
        mydefs = loadyaml('vcp.yml')
        interfaces_netbox_format = []
        try:
            vcp_table = mydefs['VcpTable'](self.device).get()
        except RpcError:
            logger.info('Not a VC - ignoring')
            return []
        for fpc in vcp_table:
            fpc_id = fpc.name.replace("fpc", "")
            for port in fpc.members:
                interface_name = "vcp-" + fpc_id + "/" + port.id
                interface_netbox_format = {'description': '',
                                           'enabled': True,
                                           'form_factor': 5200,  # Juniper VCP'
                                           'mgmt_only': False,
                                           'mac_address': None,  # Remove any previously set MAC address
                                           'mtu': None,
                                           'name': interface_name}
                interfaces_netbox_format.append(interface_netbox_format)
        return interfaces_netbox_format

    def _vrrp(self):
        logger.info('Get Juniper VRRP')
        mydefs = loadyaml('vrrp.yml')
        try:
            return mydefs['VrrpTable'](self.device).get()

        except RpcError:
            logger.info('No VRRP running - ignoring')

    @property
    def ips(self):
        if self._ips:
            logger.info('Return cached Juniper IPs')
            return self._ips

        with self.connect():
            logger.info('Get Juniper interfaces again, this time for IPs')
            mydefs = loadyaml('interfaces.yml')
            interfaces = mydefs['InterfacesTable'](self.device).get()
            vrrp = self._vrrp()

        ips_netbox_format = []

        for interface in interfaces:
            for subinterface in interface.subinterfaces:
                for af in subinterface.af:
                    for address in af.addresses:
                        ip_netbox_format = {}
                        # Temporary, we later on replace it with the Netbox interface ID
                        ip_netbox_format['interface_name'] = subinterface.name

                        # Juniper returns networks as for example: "192.168.1/24" or None if a /32 /128
                        # Some other system "IP" are purely incorrect, eg. "0x4"
                        try:
                            ip = ipaddress.ip_address(address.ip)
                            if address.network is None and ip.version == 4:
                                netmask = '32'
                            elif address.network is None and ip.version == 6:
                                netmask = '128'
                            else:
                                netmask = address.network.split('/')[1]
                            interface_ip = ipaddress.ip_interface(address.ip + '/' + netmask)
                        except (ValueError, IndexError):
                            logger.info('Invalid IP (' + str(address.ip) + '), skipping.')
                            continue

                        # Ignore link-local IPs (as they're automatic and not manually configured)
                        if address.ip.startswith('fe80::'):
                            continue

                        if 'lo0' in subinterface.name:
                            ip_netbox_format['role'] = 10
                        # If the IP is a VRRP VIP, set it so. As well as its VRRP group
                        if vrrp:
                            for vrrp_int in vrrp:
                                # We only set the VIP on the master
                                if (vrrp_int.state == 'master'
                                        and str(interface_ip.ip) in vrrp_int.virtual_ip_address
                                        and vrrp_int.interface == subinterface.name):
                                    ip_netbox_format['role'] = 41
                                    ip_netbox_format['custom_fields'] = {}
                                    ip_netbox_format['custom_fields']['group_id'] = vrrp_int.group

                        ip_netbox_format['address'] = interface_ip.with_prefixlen
                        ips_netbox_format.append(ip_netbox_format)
        return ips_netbox_format

    @property
    def interfaces(self):
        if self._interfaces:
            logger.info('Return cached Juniper interfaces')
            return self._interfaces

        with self.connect():
            logger.info('Get Juniper interfaces')
            mydefs = loadyaml('interfaces.yml')
            interfaces = mydefs['InterfacesTable'](self.device).get()
            vlans = self._vlans()
            interfaces_netbox_format = self._vcp()
            for interface in interfaces:
                # Only care about the interfaces in whitelist, and ignore the blacklist ones
                if (any(wl in interface.name for wl in interfaces_whitelist) and not any(bl in interface.name for bl in interfaces_blacklist)):
                    # Format the interface dict the way netbox expects it
                    # Note, don't set the device ID field here, but during the compare so we don't call a NetboxDevice here
                    interface_netbox_format = {'description': '',
                                               # if not interface.description or interface.description == 'DISABLED' else interface.description,
                                               'enabled': False if interface.admin == 'down' else True,
                                               'form_factor': self.intf_name_to_form_factor(interface.name),
                                               'lag': self.get_parent(interface.name),
                                               'mgmt_only': self.is_mgmt(interface.name),
                                               'mac_address': None,  # Remove any previously set MAC address
                                               'mtu': None if interface.mtu == 'Unlimited' or int(interface.mtu) == 1514 else int(interface.mtu),
                                               'name': interface.name}
                    interfaces_netbox_format.append(interface_netbox_format)

                    for subinterface in interface.subinterfaces:

                        # TODO: Remove duplicate code? How
                        if (any(wl in subinterface.name for wl in interfaces_whitelist) and not any(bl in subinterface.name for bl in interfaces_blacklist)):
                            interface_netbox_format = {'description': '' if not subinterface.description else subinterface.description,
                                                       'enabled': False if interface.admin == 'down' else True,
                                                       'form_factor': self.intf_name_to_form_factor(subinterface.name),
                                                       'mgmt_only': self.is_mgmt(subinterface.name),
                                                       'mac_address': None,  # Remove any previously set MAC address
                                                       'name': subinterface.name}
                            if subinterface.name in vlans:
                                interface_netbox_format.update(vlans[subinterface.name])

                            interfaces_netbox_format.append(interface_netbox_format)

        for interface in interfaces_netbox_format:
            if 'ae' in interface['name']:
                interfaces_netbox_format.insert(0, interfaces_netbox_format.pop(interfaces_netbox_format.index(interface)))
        self._interfaces = interfaces_netbox_format
        return self._interfaces

    # Takes an interface name as input and returns if yes or no it's a management interface
    # interface_name (string): Juniper formated interface name
    # return (bool): True or False
    @staticmethod
    def is_mgmt(interface_name):
        return any(mgmt_interfaces_name in interface_name
                   for mgmt_interfaces_name in mgmt_interfaces_names)


class DeviceDiff():
    def __init__(self, juniper_device, netbox_device):
        self.netbox_device = netbox_device
        self.juniper_device = juniper_device

    def ips(self):
        ips_compared = {}
        ips_compared['only_netbox'] = []
        ips_compared['only_juniper'] = []
        ips_compared['to_update'] = []
        juniper_ips = []
        juniper_ips_addresses_only = []
        juniper_ips_no_interface_id = self.juniper_device.ips

        for juniper_ip in juniper_ips_no_interface_id:
            # TODO we assume for now that all IPs of a VC/cluster will be on the master,
            # pfw might be an exception where it's not true
            # vc_master = self.netbox_device.virtual_chassis_master  # If any
            # if vc_master:
            #     ip['device'] = vc_master.id
            # else:  # not a VC
            #     ip['device'] = self.netbox_device.device.id
            juniper_ip['interface'] = self.netbox_device.get_interface_id(juniper_ip['interface_name'])
            # Ignore IP if no matching Netbox interface (eg. internal interfaces)
            if not juniper_ip['interface']:
                continue
            del juniper_ip['interface_name']
            juniper_ips.append(juniper_ip)
            juniper_ips_addresses_only.append(juniper_ip['address'])

        netbox_ips_addresses_only = []
        netbox_ips = self.netbox_device.ips
        for netbox_ip in netbox_ips:
            netbox_ips_addresses_only.append(netbox_ip['address'])

        # TODO remove duplcate code (IP, interfaces) by using a dedicated function that takes list 1, list 2 and key
        # returns ips_compared
        only_netbox_addresses = set(netbox_ips_addresses_only) - set(juniper_ips_addresses_only)
        if only_netbox_addresses:
            logging.info('IPs in Netbox but not on device:')
            logging.info(only_netbox_addresses)

            for netbox_ip in netbox_ips:
                if netbox_ip['address'] in only_netbox_addresses:
                    ips_compared['only_netbox'].append(netbox_ip)

        only_juniper_addresses = set(juniper_ips_addresses_only) - set(netbox_ips_addresses_only)
        if only_juniper_addresses:
            logging.info('IPs on device but not in Netbox:')
            logging.info(only_juniper_addresses)

            for juniper_ip in juniper_ips:
                if juniper_ip['address'] in only_juniper_addresses:
                    ips_compared['only_juniper'].append(juniper_ip)

        # TODO also check IPs assigned to other devices (and no devices)
        ips_both = set(netbox_ips_addresses_only) & set(juniper_ips_addresses_only)
        if ips_both:
            logging.info('IPs with differences:')
            found = False
            for netbox_ip in netbox_ips:
                for juniper_ip in juniper_ips:
                    if netbox_ip['address'] == juniper_ip['address'] and juniper_ip['address'] in ips_both:
                        compare_result = self.netbox_device.compare_dicts(netbox_ip, juniper_ip)
                        if compare_result:
                            found = True
                            logging.info(netbox_ip['address'])
                            logging.info(compare_result)
                            ips_compared['to_update'].append(juniper_ip)
            if not found:
                logging.info('None')
        return ips_compared

    def interfaces(self):
        interfaces_compared = {}
        interfaces_compared['only_netbox'] = []
        interfaces_compared['only_juniper'] = []
        interfaces_compared['to_update'] = []
        juniper_interfaces = []

        vc_position_to_id = {}

        juniper_interfaces_names = []

        juniper_interfaces_no_device_id = self.juniper_device.interfaces
        # TODO move to dedicated function so ips() can use it too?
        vc_master = self.netbox_device.virtual_chassis_master  # If any
        if vc_master:  # If we're dealing with a VC, Build a mapping of vc_position:device_id
            for vc_member in self.netbox_device.virtual_chassis_members:
                vc_position_to_id[int(vc_member.vc_position)] = vc_member.id

        for interface in juniper_interfaces_no_device_id:
            if vc_master:  # If we're dealing with a VC
                device_found = False
                # Add LACP and mgmt interfaces to master
                if any(master_interfaces_name in interface['name']
                        for master_interfaces_name in master_interfaces_names):
                    interface['device'] = vc_master.id
                    device_found = True
                else:  # Any other kind of interface should be matched to the proper VC member
                    # "Guess" the member based on the interface name and VC position
                    vcp_number = int(interface['name'].split('-')[1].split('/')[0])
                    if vcp_number in vc_position_to_id:
                        interface['device'] = vc_position_to_id[vcp_number]
                        device_found = True
                # Stop if an interface doesn't have a VC member
                if not device_found:
                    logger.error('Can\'t find VC member for interface: ' + interface['name'])
                    sys.exit(1)
            else:  # Standalone device
                interface['device'] = self.netbox_device.device.id

            # TODO we're doing it in too locations (here and create_interfaces too)
            # Should probably simplify it, but how?
            if 'lag' in interface and interface['lag']:
                parent_interface_id = self.netbox_device.get_interface_id(interface['lag'])
                if parent_interface_id:
                    interface['lag'] = parent_interface_id

            # We have to replace the VLAN ID (from Juniper, that we will call tag here for clarity)
            # with the Netbox vlan unique ID, our vlans are unique
            # infra wide, so we can be lazy about it. Otherwise we could filter per site
            if 'untagged_vlan' in interface:
                interface['untagged_vlan'] = self.netbox_device.get_vlan_id(interface['untagged_vlan'],)
            vlan_ids = []
            if 'tagged_vlans' in interface:
                for vlan_tag in interface['tagged_vlans']:
                    vlan_ids.append(self.netbox_device.get_vlan_id(vlan_tag))
                interface['tagged_vlans'] = vlan_ids
            juniper_interfaces.append(interface)
            juniper_interfaces_names.append(interface['name'])

        netbox_interfaces_names = []
        netbox_interfaces = self.netbox_device.interfaces
        for netbox_interface in netbox_interfaces:
            netbox_interfaces_names.append(netbox_interface['name'])

        only_netbox_names = set(netbox_interfaces_names) - set(juniper_interfaces_names)
        if only_netbox_names:
            logging.info('Interfaces in netbox but not on device:')
            logging.info(only_netbox_names)

            for netbox_interface in netbox_interfaces:
                if netbox_interface['name'] in only_netbox_names:
                    interfaces_compared['only_netbox'].append(netbox_interface)

        only_juniper_names = set(juniper_interfaces_names) - set(netbox_interfaces_names)
        if only_juniper_names:
            logging.info('Interfaces on device but not in Netbox:')
            logging.info(only_juniper_names)

            for juniper_interface in juniper_interfaces:
                if juniper_interface['name'] in only_juniper_names:
                    interfaces_compared['only_juniper'].append(juniper_interface)

        interfaces_both = set(netbox_interfaces_names) & set(juniper_interfaces_names)
        if interfaces_both:
            logging.info('Interfaces with differences:')
            for netbox_interface in netbox_interfaces:
                for juniper_interface in juniper_interfaces:
                    if (netbox_interface['name'] == juniper_interface['name']
                            and juniper_interface['name'] in interfaces_both):
                        compare_result = self.netbox_device.compare_dicts(netbox_interface, juniper_interface)
                        if compare_result:
                            logging.info(netbox_interface['name'])
                            logging.info(compare_result)
                            interfaces_compared['to_update'].append(juniper_interface)
        return interfaces_compared


def main():
    arg_parser = argparse.ArgumentParser(description='Import network devices entities into Netbox.')
    arg_parser.add_argument('--dryrun', help='Don\'t make change in Netbox', action='store_true')

    args = arg_parser.parse_args()
    with open('config.yml', "r") as config_file:
        config = yaml.safe_load(config_file)

    for netbox_name, fqdn in config['devices'].items():
        logger.info('Processing ' + netbox_name)
        juniper_device = JuniperDevice(fqdn, config['network'])
        netbox_device = NetboxDevice(netbox_name, config['netbox'])
        device_diff = DeviceDiff(juniper_device, netbox_device)

        interfaces_compared = device_diff.interfaces()
        if not args.dryrun:
            if interfaces_compared['only_juniper']:
                netbox_device.create_interfaces(interfaces_compared['only_juniper'])
            if interfaces_compared['only_netbox']:
                netbox_device.delete_interfaces(interfaces_compared['only_netbox'])
            if interfaces_compared['to_update']:
                netbox_device.update_interfaces(interfaces_compared['to_update'])

        ips_compared = device_diff.ips()
        if not args.dryrun:
            if ips_compared['only_juniper']:
                netbox_device.create_ips(ips_compared['only_juniper'])
            if ips_compared['only_netbox']:
                netbox_device.delete_ips(ips_compared['only_netbox'])
            if ips_compared['to_update']:
                netbox_device.update_ips(ips_compared['to_update'])


if __name__ == '__main__':
    main()
