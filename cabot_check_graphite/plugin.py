from django.conf import settings
from django.core.mail import send_mail
from django.core.urlresolvers import reverse
from django import forms
from django.template import Context, Template

from cabot.plugins.models import StatusCheckPlugin
from cabot.cabotapp.models import StatusCheckResult

from os import environ as env
import subprocess
import requests
import logging
import itertools
from .graphite import parse_metric, get_data

import logging
logger = logging.getLogger(__name__)

CHECK_TYPES = (
    ('>', 'Greater than'),
    ('>=', 'Greater than or equal'),
    ('<', 'Less than'),
    ('<=', 'Less than or equal'),
    ('==', 'Equal to'),
)

class GraphiteStatusCheckForm(forms.Form):
    metric = forms.CharField(
        help_text='fully.qualified.name of the Graphite metric you want to watch. This can be any valid Graphite expression, including wildcards, multiple hosts, etc.',
    )
    check_type = forms.ChoiceField(
        choices=CHECK_TYPES,
    )
    value = forms.IntegerField(
        help_text='If this expression evaluates to true, the check will fail (possibly triggering an alert).',
    )
    expected_num_hosts = forms.IntegerField(
        initial=0,
        help_text='The minimum number of data series (hosts) you expect to see.',
    )
    allowed_num_failures = forms.IntegerField(
        initial=0,
        help_text='The maximum number of data series (metrics) you expect to fail. For example, you might be OK with 2 out of 3 webservers having OK load (1 failing), but not 1 out of 3 (2 failing).',
    )


class GraphiteStatusCheckPlugin(StatusCheckPlugin):
    name = "Graphite"
    slug = "graphite-check"
    author = "Jonathan Balls"
    version = "0.0.1"
    font_icon = "glyphicon glyphicon-signal"

    config_form = GraphiteStatusCheckForm

    def format_error_message(self, check, failures, actual_hosts, hosts_by_target):
        if actual_hosts < check.expected_num_hosts:
            return "Hosts missing | %d/%d hosts" % (
                actual_hosts, check.expected_num_hosts)
        elif actual_hosts > 1:
            threshold = float(check.value)
            failures_by_host = ["%s: %s %s %0.1f" % (
                hosts_by_target[target], value, check.check_type, threshold)
                for target, value in failures]
            return ", ".join(failures_by_host)
        else:
            target, value = failures[0]
            return "%s %s %0.1f" % (value, check.check_type, float(check.value))

    def run(self, check, result):

        failures = []
        graphite_output = parse_metric(check.metric, mins_to_check=check.frequency)

        try:
            result.raw_data = json.dumps(graphite_output['raw'])
        except:
            result.raw_data = graphite_output['raw']

        if graphite_output["error"]:
            result.succeeded = False
            result.error = graphite_output["error"]
            return result

        if graphite_output['num_series_with_data'] > 0:
            result.average_value = graphite_output['average_value']
            for s in graphite_output['series']:
                if not s["values"]:
                    continue
                failure_value = None
                if check.check_type == '<':
                    if float(s['min']) < float(check.value):
                        failure_value = s['min']
                elif check.check_type == '<=':
                    if float(s['min']) <= float(check.value):
                        failure_value = s['min']
                elif check.check_type == '>':
                    if float(s['max']) > float(check.value):
                        failure_value = s['max']
                elif check.check_type == '>=':
                    if float(s['max']) >= float(check.value):
                        failure_value = s['max']
                elif check.check_type == '==':
                    if float(check.value) in s['values']:
                        failure_value = float(check.value)
                else:
                    raise Exception(u'Check type %s not supported' %
                                    check.check_type)

                if not failure_value is None:
                    failures.append((s["target"], failure_value))

        if len(failures) > check.allowed_num_failures:
            result.succeeded = False
        elif graphite_output['num_series_with_data'] < check.expected_num_hosts:
            result.succeeded = False
        else:
            result.succeeded = True

        if not result.succeeded:
            targets = [s["target"] for s in graphite_output["series"]]
            hosts = minimize_targets(targets)
            hosts_by_target = dict(zip(targets, hosts))

            result.error = self.format_error_message(
		check,
                failures,
                graphite_output['num_series_with_data'],
                hosts_by_target,
            )

        return result

def minimize_targets(targets):
    split = [target.split(".") for target in targets]

    prefix_nodes_in_common = 0
    for i, nodes in enumerate(itertools.izip(*split)):
        if any(node != nodes[0] for node in nodes):
            prefix_nodes_in_common = i
            break
    split = [nodes[prefix_nodes_in_common:] for nodes in split]

    suffix_nodes_in_common = 0
    for i, nodes in enumerate(reversed(zip(*split))):
        if any(node != nodes[0] for node in nodes):
            suffix_nodes_in_common = i
            break
    if suffix_nodes_in_common:
        split = [nodes[:-suffix_nodes_in_common] for nodes in split]

    return [".".join(nodes) for nodes in split]

