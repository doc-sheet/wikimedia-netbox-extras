#!/usr/bin/env python3
"""
ganeti-netbox-sync

This script synchronizes ganeti Instance state with Netbox virtualization.virtual_hosts state, and
assigns Netbox devices which match Ganeti nodes to Netbox's dcim.device.cluster.
"""

import argparse
import json
import logging
import sys

from collections import Counter
from configparser import ConfigParser

import pynetbox
import requests
from requests.auth import HTTPBasicAuth


NO_PUPPETDB_VMS = ('d-i-test',)
logger = logging.getLogger()


def parse_command_line_args():
    """Parse command line options."""
    parser = argparse.ArgumentParser()

    parser.add_argument("profile", help="The profile to use from the configuration file.")
    parser.add_argument(
        "-i",
        "--instances-json",
        help=(
            "the path of a JSON file with the output of the Ganeti RAPI `instances&bulk=1` endpoint to process "
            "instead of accessing the API directly."
        ),
    )
    parser.add_argument(
        "-n",
        "--nodes-json",
        help=(
            "the path of a JSON file with the output of the Ganeti RAPI `nodes` endpoint to process instead of "
            "accessing the API directly."
        ),
    )
    parser.add_argument(
        "-c", "--config", help="The path to the config file to load.", default="/etc/netbox/ganeti-sync.cfg"
    )
    parser.add_argument(
        "-d", "--dry-run", help="Don't actually commit any changes, just do a dry-run", action="store_true"
    )
    parser.add_argument("-v", "--verbose", help="Output more verbosity.", action="store_true")

    args = parser.parse_args()

    # validation and manipulation
    if args.dry_run:
        args.verbose = True

    return args


def setup_logging(verbose=False):
    """Setup the logging with a custom format to go to stdout."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(format="%(asctime)s [%(levelname)s] %(message)s", level=level)
    logging.getLogger("requests").setLevel(logging.WARNING)  # Silence noisy logger


def ganeti_rapi_query(endpoint, base_url, user, password, ca_cert):
    """Execute the GET verb on a specified Ganeti endpoint."""
    target_url = "/".join((base_url.strip("/"), "2", endpoint))
    r = requests.get(target_url, auth=HTTPBasicAuth(user, password), verify=ca_cert, timeout=30)
    if r.status_code != 200:
        raise Exception("Can't access Ganeti API {} {}".format(r.status_code, r.text))
    return r.json()


def ganeti_host_to_netbox(ganeti_dict, additional_fields):
    """Takes a single entry from the Ganeti host list and returns just the fields pertinent to Netbox
    along with any additional fields that need to be added"""
    shortname = ganeti_dict["name"].split(".")[0]
    output = {
        "name": shortname,
        "vcpus": ganeti_dict["beparams"]["vcpus"],
        "memory": ganeti_dict["beparams"]["memory"],
        "disk": round(sum(ganeti_dict["disk.sizes"]) / 1024, 0),  # ganeti gives megabytes, netbox expects gigabytes
    }
    # admin_state is the desired state of the machine, which maps nicely to the status field.
    if ganeti_dict["admin_state"] == "up":
        output["status"] = "active"
    else:
        output["status"] = "offline"

    output.update(additional_fields)
    return output


def sync_ganeti_netbox_host_diff(ganeti_host, netbox_host):
    """Update fields on netbox_host from ganeti_dict, return True if updates are made."""
    updated = False
    if netbox_host.vcpus != ganeti_host["vcpus"]:
        logger.debug("updating vcpus on %s %d -> %d", netbox_host.name, netbox_host.vcpus, ganeti_host["vcpus"])
        netbox_host.vcpus = ganeti_host["vcpus"]
        updated = True
    if netbox_host.memory != ganeti_host["memory"]:
        logger.debug("updating memory on %s %d -> %d", netbox_host.name, netbox_host.memory, ganeti_host["memory"])
        netbox_host.memory = ganeti_host["memory"]
        updated = True
    if netbox_host.disk != ganeti_host["disk"]:
        logger.debug("updating disk on %s %d -> %d", netbox_host.name, netbox_host.disk, ganeti_host["disk"])
        netbox_host.disk = ganeti_host["disk"]
        updated = True
    if netbox_host.status.value != ganeti_host["status"]:
        logger.debug(
            "updating status on %s %d -> %d", netbox_host.name, netbox_host.status.value, ganeti_host["status"]
        )
        netbox_host.status = ganeti_host["status"]
        updated = True
    if netbox_host.cluster.id != ganeti_host["cluster"]:
        logger.debug(
            "updating cluster on %s %d -> %d", netbox_host.name, netbox_host.cluster.id, ganeti_host["cluster"]
        )
        netbox_host.cluster = ganeti_host["cluster"]
        updated = True

    return updated


def sync_ganeti_to_netbox(netbox_api, netbox_token, cluster_group_name, ganeti_hosts, ganeti_nodes, dryrun):
    """Preform a sync from the Ganeti host (VM) list into the specified Netbox API destination."""
    nbapi = pynetbox.api(netbox_api, token=netbox_token)

    nb_linux_id = nbapi.dcim.platforms.get(slug="linux").id
    nb_server_id = nbapi.dcim.device_roles.get(slug="server").id

    nb_cluster_group = nbapi.virtualization.cluster_groups.get(name=cluster_group_name)
    if not nb_cluster_group:
        logger.error(f'Cluster group "{cluster_group_name}" doesn\'t exist in Netbox')
        return
    nb_cluster_group_id = nb_cluster_group.id

    nb_clusters = {}  # Cluster-name:cluster-id
    for cluster in nbapi.virtualization.clusters.filter(group_id=nb_cluster_group_id):
        nb_clusters[cluster.name] = cluster.id
    # List of all Netbox cluster IDs in a given cluster group
    nb_clusters_ids = list(nb_clusters.values())

    nb_vhosts = {}
    # make a convenient dictionary of all Netbox VMs in this cluster group
    if nb_clusters_ids:  # If empty list, returns all the Netbox VMs
        for host in nbapi.virtualization.virtual_machines.filter(cluster_id=nb_clusters_ids):
            nb_vhosts[host.name] = host

    results = Counter()
    for nb_host_name, nb_host in nb_vhosts.items():
        if nb_host_name not in ganeti_hosts:
            if not dryrun:
                nb_host.delete()
            logger.debug("removed %s from netbox", nb_host_name)
            results["del"] += 1
        else:
            try:
                # Lookup which cluster the VM's hypervisor is part of
                nb_cluster_name = ganeti_nodes[ganeti_hosts[nb_host_name]['pnode']]
                ganeti_host = ganeti_host_to_netbox(ganeti_hosts[nb_host_name],
                                                    {"cluster": nb_clusters[nb_cluster_name]})  # Cluster ID
            except (KeyError, TypeError) as e:
                logger.error(
                    "Host %s raised an exception when trying to convert to Netbox for syncing: %s", nb_host_name, e
                )
                continue

            try:
                diff_result = sync_ganeti_netbox_host_diff(ganeti_host, nb_host)
            except KeyError as e:
                logger.error("Host %s raised an exception when trying to resolve diff: %s", nb_host_name, e)
                continue
            if diff_result:
                logger.debug("updating %s in netbox", nb_host_name)
                if not dryrun:
                    nb_host.save()
                results["update"] += 1

    logger.info("removed %s instances from netbox", results["del"])
    logger.info("updated %s instances from netbox", results["update"])

    # Itterate over all Ganeti VMs of a cluster group:
    # if they don't exist in the matching Netbox cluster group
    # try to find if it exists in a different cluster, if so move it, if not create it.
    # This is to prevent creating duplicate VMs in different group, especially when renaming clusters
    # while being more efficient than always loading all the VMs
    for ganeti_host_name, ganeti_host in ganeti_hosts.items():
        # If it already exists in the good cluster group, don't do anything
        if ganeti_host_name in nb_vhosts:
            continue
        # Get the cluster name based on the node (hypervisor) the VM is on
        nb_cluster_name = ganeti_nodes[ganeti_host['pnode']]
        try:
            nb_vm = nbapi.virtualization.virtual_machines.get(name=ganeti_host_name)
            if nb_vm:
                logger.debug(f"Moving {ganeti_host_name} to cluster {nb_cluster_name}")
                results["move"] += 1
                if not dryrun:
                    nb_vm.cluster = nb_clusters[nb_cluster_name]
                    nb_vm.save()
                continue
        except ValueError:
            logger.error(f'More than 1 instance named {ganeti_host_name}. Please correct it manually.')
            continue
        try:
            ganeti_host_dict = ganeti_host_to_netbox(
                ganeti_host,
                {"cluster": nb_clusters[nb_cluster_name], "platform": nb_linux_id, "role": nb_server_id},
            )
        except (KeyError, TypeError) as e:
            logger.error(
                "Host %s raised an exception when trying to convert to Netbox for adding: %s", ganeti_host_name, e
            )
            continue
        if dryrun:
            save_result = True
        else:
            try:
                save_result = nbapi.virtualization.virtual_machines.create(ganeti_host_dict)
            except pynetbox.RequestError as e:
                logger.error("Host %s raised an exception when trying to create in Netbox: %s", ganeti_host_name, e)
                continue

        if save_result:
            results["add"] += 1
            logger.debug("adding %s to netbox", ganeti_host_dict["name"])
        else:
            logger.error("error added %s to netbox", ganeti_host_dict["name"])
    logger.info(f'Moved {results["move"]} instances to a different cluster group.')
    logger.info("added %s instances to netbox", results["add"])


def sync_ganeti_nodes_to_netbox(netbox_api, netbox_token, cluster_group_name, ganeti_groups, dry_run):
    """Perform a sync between the Ganeti Node (hypervisors) list and the Netbox API"""
    nbapi = pynetbox.api(netbox_api, token=netbox_token)
    nb_ganeti_id = nbapi.virtualization.cluster_types.get(slug="ganeti").id

    # Create the Netbox cluster group if it doesn't exist
    nb_cluster_group = nbapi.virtualization.cluster_groups.get(name=cluster_group_name)
    if not nb_cluster_group:
        if not dry_run:
            logger.info(f"Creating cluster group {cluster_group_name}")
            nb_cluster_group = nbapi.virtualization.cluster_groups.create(name=cluster_group_name,
                                                                          slug=cluster_group_name)
        else:
            logger.info(f"Need to create cluster group {cluster_group_name} but in dry-run.")
            return
    nb_cluster_group_id = nb_cluster_group.id

    results = Counter()
    for nb_cluster_name, nodes in ganeti_groups.items():
        # Create the netbox cluster (Ganeti group) if it doesn't exist
        nb_cluster = nbapi.virtualization.clusters.get(name=nb_cluster_name, group_id=nb_cluster_group_id)
        if not nb_cluster:
            if not dry_run:
                logger.info(f"Creating cluster {nb_cluster_name}")
                nb_cluster = nbapi.virtualization.clusters.create(name=nb_cluster_name,
                                                                  type=nb_ganeti_id,
                                                                  group=nb_cluster_group_id)
            else:
                logger.info(f"Need to create cluster {nb_cluster_name} but in dry-run.")
                return
        nb_cluster_id = nb_cluster.id

        nb_cluster_nodes = {n.name: n for n in nbapi.dcim.devices.filter(cluster_id=nb_cluster_id)}
        for node in nodes:
            if node not in nb_cluster_nodes.keys():
                try:
                    device = nbapi.dcim.devices.get(name=node)
                except pynetbox.RequestError as e:
                    logger.error("an error was raised trying to retrieve host %s: %s", node, e)
                    continue

                if device is not None:
                    device.cluster = nb_cluster_id
                    if dry_run:
                        save_result = True
                    else:
                        try:
                            save_result = device.save()
                        except pynetbox.RequestError as e:
                            logger.error("an error was raised trying to save host %s: %s", node, e)
                            continue

                    if save_result:
                        results["assign"] += 1
                        logger.debug("assigned %s to %s", node, nb_cluster_name)
                    else:
                        logger.error("error assigning %s to %s", node, nb_cluster_name)
                else:
                    logger.error("device %s does not exist in netbox to assign to cluster %s", node, nb_cluster_name)

    logger.info("assigned %d hosts to cluster %s", results["assign"], nb_cluster_name)

    for group_name, nodes in ganeti_groups.items():
        # If a node is moved between cluster, first run will remove its cluster, 2nd will add it to the correct one
        # This action is not frequent enough to be problematic, but a TODO is to add a "move node" function
        nb_cluster_id = nbapi.virtualization.clusters.get(name=group_name, group_id=nb_cluster_group_id).id
        nb_cluster_nodes = {n.name: n for n in nbapi.dcim.devices.filter(cluster_id=nb_cluster_id)}
        removals = set(nb_cluster_nodes.keys()) - set(nodes)
        for node in removals:
            device = nb_cluster_nodes[node]
            device.cluster = None
            if dry_run:
                save_result = True
            else:
                try:
                    save_result = device.save()
                except pynetbox.RequestError as e:
                    logger.error("an error was raised trying to save host %s: %s", node, e)
                    continue
            if save_result:
                results["remove"] += 1
                logger.debug("removed %s from %s", node, group_name)
            else:
                logger.error("error removing %s from %s", node, group_name)

    logger.info("removed %d nodes from cluster %s", results["remove"], group_name)


def run_import_script(netbox_api, netbox_token, imports, dry_run):
    """Execute the PuppetDB import script for a given list of hosts."""
    api_url = '{}api/extras/scripts/{}/'.format(netbox_api, 'interface_automation.ImportPuppetDB')
    headers = {'Authorization': 'Token {}'.format(netbox_token)}
    data = {"data": {"device": " ".join(imports)}, "commit": not dry_run}
    result = requests.post(api_url, headers=headers, json=data)
    if result.status_code == 200:
        logger.debug("Executed import, result: %s", str(result.content))
    else:
        logger.error("Error executing import. Data: %s", str(result.content))


def nb_fix_ganeti_interfaces(netbox_api, netbox_token, netbox_cluster_group, dry_run):
    """Execute PuppetDB import on any host in a list of hosts with a placeholder Interface."""
    nbapi = pynetbox.api(netbox_api, token=netbox_token)
    try:
        nb_clusters = nbapi.virtualization.clusters.filter(group=netbox_cluster_group)
    except pynetbox.RequestError as e:
        logger.error(f'Error while loading clusters from Netbox: {e}')
        return
    reimport = [str(x.virtual_machine) for x in
                nbapi.virtualization.interfaces.filter(name='##PRIMARY##', cluster__in=nb_clusters)
                if x.virtual_machine.name not in NO_PUPPETDB_VMS]
    if reimport:
        logger.info("Will import interfaces for {} instances.".format(len(reimport)))
        run_import_script(netbox_api, netbox_token, reimport, dry_run)


def main():
    """Entry point for Ganeti->Netbox Sync."""
    args = parse_command_line_args()
    setup_logging(args.verbose)

    # Load configuration
    cfg = ConfigParser()
    cfg.read(args.config)
    logger.info("loaded %s configuration", args.config)
    netbox_token = cfg["auth"]["netbox_token"]
    ganeti_user = cfg["auth"]["ganeti_user"]
    ganeti_password = cfg["auth"]["ganeti_password"]
    netbox_api = cfg["netbox"]["api"]
    ganeti_api = cfg["profile:" + args.profile]["api"]
    ganeti_ca_cert = cfg["auth"]["ca_cert"]

    logger.debug("using ganeti api at %s", ganeti_api)
    logger.debug("using netbox api at %s", netbox_api)

    if args.dry_run:
        logger.info("*** DRY RUN ***")

    # Sync Hosts
    if args.instances_json:
        logger.info("note: loading json file rather than accessing ganeti api %s", args.instances_json)
        with open(args.instances_json, "r") as in_json:
            ganeti_hosts_json = json.load(in_json)
    else:
        ganeti_hosts_json = ganeti_rapi_query(
            "instances?bulk=1", ganeti_api, ganeti_user, ganeti_password, ganeti_ca_cert
        )

    # Convert instances JSON to a dict keyed by the host name
    ganeti_hosts = {i["name"].split(".")[0]: i for i in ganeti_hosts_json}
    logger.debug("loaded %d instances from ganeti api", len(ganeti_hosts))

    # Sync Netbox cluster (Ganeti group) and their nodes
    if args.nodes_json:
        logger.info("note: loading json file rather than accessing ganeti api %s", args.nodes_json)
        with open(args.nodes_json, "r") as in_json:
            ganeti_nodes_json = json.load(in_json)
    else:
        ganeti_nodes_json = ganeti_rapi_query("groups?bulk=1", ganeti_api, ganeti_user, ganeti_password, ganeti_ca_cert)

    ganeti_groups = {}  # Convert groups JSON to dict with key: cluster name (site-group) and value: node device name
    ganeti_nodes = {}  # Convert groups JSON to dict with key: node FQDN and value: netbox cluster name
    nodes_count = 0
    for group in ganeti_nodes_json:
        nodes_count += len(group['node_list'])
        nb_cluster_name = group['name']  # aka Ganeti group name
        ganeti_groups[nb_cluster_name] = [x.split(".", 1)[0] for x in group['node_list']]
        for node in group['node_list']:
            ganeti_nodes[node] = nb_cluster_name

    logger.debug(f"loaded {nodes_count} nodes and {len(ganeti_groups)} groups from Ganeti API")

    sync_ganeti_nodes_to_netbox(netbox_api, netbox_token, args.profile, ganeti_groups, args.dry_run)
    sync_ganeti_to_netbox(netbox_api, netbox_token, args.profile, ganeti_hosts, ganeti_nodes, args.dry_run)
    nb_fix_ganeti_interfaces(netbox_api, netbox_token, args.profile, args.dry_run)

    return 0


if __name__ == "__main__":
    sys.exit(main())
