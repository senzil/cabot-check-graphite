# -*- coding: utf-8 -*-

from django.contrib.auth.models import User
from cabot.cabotapp.tests.tests_basic import LocalTestCase
from cabot.cabotapp.models import StatusCheck, Instance
from cabot.plugins.models import StatusCheckPluginModel
from cabot_check_graphite.plugin import GraphiteStatusCheckPlugin, minimize_targets
from cabot.cabotapp.models import Service, StatusCheckResult
from mock import Mock, patch
import os
from .graphite import parse_metric
import json
import time

from logging import getLogger
logger = getLogger(__name__)

def get_content(fname):
    path = os.path.join(os.path.dirname(__file__), 'fixtures/%s' % fname)
    with open(path) as f:
        return f.read()

def fake_graphite_response(*args, **kwargs):
    resp = Mock()
    resp.json = lambda: json.loads(get_content('graphite_response.json'))
    resp.status_code = 200
    return resp


def fake_graphite_series_response(*args, **kwargs):
    resp = Mock()
    resp.json = lambda: json.loads(get_content('graphite_avg_response.json'))
    resp.status_code = 200
    return resp


def fake_empty_graphite_response(*args, **kwargs):
    resp = Mock()
    resp.json = lambda: json.loads(get_content('graphite_null_response.json'))
    resp.status_code = 200
    return resp


def fake_slow_graphite_response(*args, **kwargs):
    resp = Mock()
    time.sleep(0.1)
    resp.json = lambda: json.loads(get_content('graphite_null_response.json'))
    resp.status_code = 200
    return resp

class TestGraphiteCheckCheckPlugin(LocalTestCase):

    def setUp(self):
        super(TestGraphiteCheckCheckPlugin, self).setUp()

        self.graphite_check_model, created = StatusCheckPluginModel.objects.get_or_create(
	    slug='cabot_check_graphite'
	    )

        self.graphite_check = StatusCheck.objects.create(
            check_plugin=self.graphite_check_model,
            name='Graphite Check',
            metric='stats.fake.value',
            check_type='>',
            value='9.0',
            created_by=self.user,
            importance=Service.ERROR_STATUS,
        )

        self.graphite_check.save()
        self.graphite_check = StatusCheck.objects.get(pk=self.graphite_check.pk)
	self.service.status_checks.add(self.graphite_check)

    @patch('cabot_check_graphite.graphite.requests.get', fake_graphite_response)
    def test_graphite_run(self):
        checkresults = self.graphite_check.statuscheckresult_set.all()
        self.assertEqual(len(checkresults), 0)
        self.graphite_check.run()
        checkresults = self.graphite_check.statuscheckresult_set.all()
        self.assertEqual(len(checkresults), 1)
        # Most recent check failed
        self.assertFalse(self.graphite_check.last_result().succeeded)
        self.assertEqual(self.graphite_check.calculated_status,
                         Service.CALCULATED_FAILING_STATUS)
        # This should now pass
        self.graphite_check.value = '11.0'
        self.graphite_check.save()
        checkresults = self.graphite_check.statuscheckresult_set.all()
        self.assertEqual(len(checkresults), 1)
        self.graphite_check.run()
        checkresults = self.graphite_check.statuscheckresult_set.all()
        self.assertEqual(len(checkresults), 2)
        self.assertEqual(self.graphite_check.calculated_status,
                         Service.CALCULATED_PASSING_STATUS)
        # As should this - passing but failures allowed
        self.graphite_check.allowed_num_failures = 2
        self.graphite_check.save()
        self.graphite_check.run()
        checkresults = self.graphite_check.statuscheckresult_set.all()
        self.assertEqual(len(checkresults), 3)
        self.assertEqual(self.graphite_check.calculated_status,
                         Service.CALCULATED_PASSING_STATUS)
        # As should this - failing but 1 failure allowed
        # (in test data, one data series is entirely below 9 and one goes above)
        self.graphite_check.value = '9.0'
        self.graphite_check.allowed_num_failures = 1
        self.graphite_check.save()
        self.graphite_check.run()
        checkresults = self.graphite_check.statuscheckresult_set.all()
        self.assertEqual(len(checkresults), 4)
        self.assertEqual(self.graphite_check.calculated_status,
                         Service.CALCULATED_PASSING_STATUS,
                         list(checkresults)[-1].error)
        # And it will fail if we don't allow failures
        self.graphite_check.allowed_num_failures = 0
        self.graphite_check.save()
        self.graphite_check.run()
        checkresults = self.graphite_check.statuscheckresult_set.all()
        self.assertEqual(len(checkresults), 5)
        self.assertEqual(self.graphite_check.calculated_status,
                         Service.CALCULATED_FAILING_STATUS)
        result = checkresults.order_by('-time')[0]
        self.assertEqual(result.error, u'PROD: 9.16092 > 9.0')

    @patch('cabot_check_graphite.graphite.requests.get', fake_graphite_series_response)
    def test_graphite_series_run(self):
        jsn = parse_metric('fake.pattern')
        self.assertEqual(jsn['average_value'], 59.86)
        self.assertEqual(jsn['series'][0]['max'], 151.0)
        self.assertEqual(jsn['series'][0]['min'], 0.1)

    @patch('cabot_check_graphite.graphite.requests.get', fake_empty_graphite_response)
    def test_graphite_empty_run(self):
        checkresults = self.graphite_check.statuscheckresult_set.all()
        self.assertEqual(len(checkresults), 0)
        self.graphite_check.run()
        checkresults = self.graphite_check.statuscheckresult_set.all()
        self.assertEqual(len(checkresults), 1)
        self.assertTrue(self.graphite_check.last_result().succeeded)
        self.assertEqual(self.graphite_check.calculated_status,
                         Service.CALCULATED_PASSING_STATUS)
        self.graphite_check.expected_num_hosts = 1
        self.graphite_check.save()
        self.graphite_check.run()
        checkresults = self.graphite_check.statuscheckresult_set.all()
        self.assertEqual(len(checkresults), 2)
        self.assertFalse(self.graphite_check.last_result().succeeded)
        self.assertEqual(self.graphite_check.calculated_status,
                         Service.CALCULATED_FAILING_STATUS)

    @patch('cabot_check_graphite.graphite.requests.get', fake_slow_graphite_response)
    def test_graphite_timing(self):
        checkresults = self.graphite_check.statuscheckresult_set.all()
        self.assertEqual(len(checkresults), 0)
        self.graphite_check.run()
        checkresults = self.graphite_check.statuscheckresult_set.all()
        self.assertEqual(len(checkresults), 1)
        self.assertTrue(self.graphite_check.last_result().succeeded)
        self.assertGreater(list(checkresults)[-1].took, 0.0)

class TestMinimizeTargets(LocalTestCase):
    def test_null(self):
        result = minimize_targets([])
        self.assertEqual(result, [])

    def test_all_same(self):
        result = minimize_targets(["a", "a"])
        self.assertEqual(result, ["a", "a"])

    def test_all_different(self):
        result = minimize_targets(["a", "b"])
        self.assertEqual(result, ["a", "b"])

    def test_same_prefix(self):
        result = minimize_targets(["prefix.a", "prefix.b"])
        self.assertEqual(result, ["a", "b"])

        result = minimize_targets(["prefix.second.a", "prefix.second.b"])
        self.assertEqual(result, ["a", "b"])

    def test_same_suffix(self):
        result = minimize_targets(["a.suffix", "b.suffix"])
        self.assertEqual(result, ["a", "b"])

        result = minimize_targets(["a.suffix.suffix", "b.suffix.suffix"])
        self.assertEqual(result, ["a", "b"])

        result = minimize_targets(["a.b.suffix.suffix", "b.c.suffix.suffix"])
        self.assertEqual(result, ["a.b", "b.c"])

    def test_same_prefix_and_suffix(self):
        result = minimize_targets(["prefix.a.suffix", "prefix.b.suffix"])
        self.assertEqual(result, ["a", "b"])

        result = minimize_targets(["prefix.prefix.a.suffix.suffix",
                                   "prefix.prefix.b.suffix.suffix",])
        self.assertEqual(result, ["a", "b"])

