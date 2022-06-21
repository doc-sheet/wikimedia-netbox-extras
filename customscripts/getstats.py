"""Produce Prometheus-formatted statistics about devices."""

from collections import Counter
from datetime import timedelta
from io import StringIO

from django.contrib.contenttypes.models import ContentType
from django.utils import timezone

from dcim.models import Device

from extras import models
from extras.choices import JobResultStatusChoices
from extras.scripts import Script


class GetDeviceStats(Script):
    class Meta:
        name = "Get Device Statistics"
        description = "Dump a set of statistics about various devices for Prometheus."

    def run(self, data, commit):
        # Delete old versions of this report
        obj_type = ContentType.objects.get_for_model(models.Script)
        name = f'{__name__}.{type(self).__name__}'
        # Keep any reports from the last 5 minutes to make this less racy
        cutoff = timezone.now() - timedelta(minutes=5)
        jobs = models.JobResult.objects.filter(
            obj_type=obj_type,
            name=name,
            status__in=JobResultStatusChoices.TERMINAL_STATE_CHOICES,
            created__lt=cutoff,
        )
        # Make sure we call delete on each job to trigger any customized delete methods
        for job in jobs:
            job.delete()
        counts = Counter()
        output = StringIO()
        for device in Device.objects.all().values_list(
            "status", "site__slug", "rack__location__slug", "device_type__manufacturer__slug"
        ):
            counts[(device[0], device[1], device[2], device[3])] += 1

        output.write("""# HELP netbox_device_count The number of devices with various properties.\n""")
        output.write("""# TYPE netbox_device_count gauge\n""")
        for params, count in counts.items():
            output.write(
                'netbox_device_count{{status="{}",datacenter="{}",rackgroup="{}",manufacturer="{}"}} {}\n'.format(
                    *params, count
                )
            )

        return output.getvalue()
