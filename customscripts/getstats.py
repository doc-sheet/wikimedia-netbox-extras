"""Produce Prometheus-formatted statistics about devices."""

from collections import Counter
from io import StringIO

from dcim.models import Device, Site, Manufacturer
from extras.scripts import Script, ObjectVar, BooleanVar

STATUSES = ['offline', 'active', 'planned', 'staged', 'failed', 'inventory', 'decommissioning']


class GetDeviceStats(Script):
    class Meta:
        name = "Get Device Statistics"
        description = "Dump a set of statistics about various devices."

    def run(self, data):
        counts = Counter()
        output = StringIO()
        for device in Device.objects.all().values_list(
                "status", "site__slug", "device_type__manufacturer__slug"
        ):
            counts[(device[0], device[1], device[2])] += 1

        output.write("""# HELP netbox_device_count The number of devices with various properties.\n""")
        output.write("""# TYPE netbox_device_count gauge\n""")
        for count in counts:
            output.write(
                """netbox_device_count{{status="{}",datacenter="{}",manufacturer="{}"}} {}\n""".format(
                    count[0], count[1], count[2], counts[count]
                )
            )

        return output.getvalue()
