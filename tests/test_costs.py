import json
from datetime import timedelta, date
from enum import StrEnum
import pandas as pd

import pytest

from domino_cost.cost import *
from domino_cost.cost_enums import CostEnums
from domino_cost.requests_helpers import get_cloud_cost_sum
from tests.conftest import dummy_hostname


class TestCostDashboard:
    def test_get_namespace(self):
        host_name = "http://thisfrontend.my-platform:80"
        host_ns = get_domino_namespace(host_name)
        assert host_ns == "my-platform"

    def test_get_time_delta(self):
        input = "lastweek"
        expected = timedelta(days=6)
        assert get_time_delta(input) == expected
        assert get_time_delta("7d") == expected

    def test_process_or_zero(self):
        def func(x: int) -> Any:
            return x + 15

        assert process_or_zero(func(15), 15) == 30
        assert process_or_zero(func(0), 15) == 15
        assert process_or_zero(func(15), 0) == 0

    def test_clean_values(self):
        input_vals = ["__unallocated__", "one", "two", "three"]
        output_vals = ["one", "two", "three"]
        assert clean_values(input_vals) == output_vals

    def test_get_execution_cost_table(self):
        with open("data/allocation.json", "r") as alloc_tf:
            allocation_data = json.load(alloc_tf)

        alloc = get_execution_cost_table(allocation_data)

        assert len(allocation_data) == 13
        assert len(alloc) == 13

    def test_get_empty_cloud_cost(self, dummy_hostname):
        """
        assert cloud cost return 0 when endpoint not found for backward compatibility.
        """
        selection = "7d"
        base_url = dummy_hostname
        headers = {}
        cc = get_cloud_cost_sum(selection, base_url, headers)
        assert cc == 0.0

    def test_to_pd_ts(self):
        input_ts = "2017-08-01T00:00:00Z"
        actual_ts = to_pd_ts(input_ts)
        expected_ts = pd.Timestamp(input_ts, tz="UTC").normalize()
        assert actual_ts == expected_ts
        assert to_pd_ts() == pd.Timestamp("today", tz="UTC").normalize()


class TestConstants:
    def test_get_enums_list(self):
        class TestEnum(CostEnums):
            One = "one"
            Two = "two"

        assert TestEnum.to_values_list() == ["one", "two"]
        assert TestEnum.One == "one"
