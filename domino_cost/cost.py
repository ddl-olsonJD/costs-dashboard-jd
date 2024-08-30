import logging
import re
from datetime import timedelta
from typing import Any
from typing import Callable
from typing import List
from typing import Union

import pandas as pd
import plotly.express as px
import plotly.graph_objs as go
from dash import dash_table
from dash.dash_table import DataTable
from pandas import DataFrame
from pandas import Timestamp

from domino_cost.constants import NO_TAG
from domino_cost.cost_enums import CostAggregatedLabels
from domino_cost.cost_enums import CostFieldsLabels
from domino_cost.cost_enums import CostLabels

logger = logging.getLogger(__name__)


def get_domino_namespace(api_host) -> str:
    pattern = re.compile("(https?://)((.*\.)*)(?P<ns>.*?):(\d*)\/?(.*)")
    match = pattern.match(api_host)
    logger.info("getting domino namespace from %s", api_host)
    return match.group("ns")


def get_today_timestamp() -> Timestamp:
    return pd.Timestamp("today", tz="UTC").normalize()


def get_time_delta(time_span) -> timedelta:
    logger.info("getting time delta from %s", time_span)
    if time_span == "lastweek":
        days_to_use = 7
    else:
        days_to_use = int(time_span[:-1])
    return timedelta(days=days_to_use - 1)


def process_or_zero(func: Callable, pos_int: float) -> Union[Callable, float]:
    if pos_int > 0.0:
        return func
    else:
        return 0.0


def distribute_cost(df: DataFrame) -> DataFrame:
    """
    distributes __unallocated__ cost for cleaner representation.
    """
    logger.info("distribute costs")
    fields_list = CostFieldsLabels.to_values_list()
    cost_unallocated = df[df["TYPE"].str.startswith("__")]
    cost_allocated = df[~df["TYPE"].str.startswith("__")]
    cost_allocated_total_sum = cost_allocated[CostFieldsLabels.ALLOC_COST.value].sum()

    for field in fields_list:
        cost_allocated[field] = cost_allocated[field] + (
                (cost_allocated[CostFieldsLabels.ALLOC_COST.value] / cost_allocated_total_sum)
                * cost_unallocated[field].sum()
        )
    return cost_allocated


def distribute_cloud_cost(df: DataFrame, cost: float) -> DataFrame:
    """
    distribute unaccounted cloud cost across allocated executions.
    """
    logger.info("distribute cloud costs %s", cost)
    accounted_cost = df[CostFieldsLabels.ALLOC_COST.value].sum()
    cloud_cost_unaccounted = process_or_zero(cost - accounted_cost, cost)

    df["CLOUD COST"] = cloud_cost_unaccounted * (df["ALLOC COST"] / accounted_cost)
    df["TOTAL COST"] = df["ALLOC COST"] + df["CLOUD COST"]

    return df


def clean_values(values_list: list) -> list:
    """
    remove "__unallocated__" from values'
    """
    return values_list[1:] if values_list[0].startswith("__") else values_list


def clean_df(df: DataFrame, col: str) -> DataFrame:
    """
    remove "__unallocated__" records from dataframe for preview.
    """
    return df[~df[col].str.startswith("__")]


def get_cumulative_cost_graph(cost_table: DataFrame, time_span: timedelta) -> dict:
    x_date_series = pd.date_range(
        get_today_timestamp() - get_time_delta(time_span),
        get_today_timestamp(),
    ).strftime("%B %-d")
    cost_table_grouped_by_date = cost_table.groupby("FORMATTED START")

    colors = ['#FF6543', '#A5C5F6', '#563FB3', '#3C3A42', '#777384', ]  # Color codes

    cumulative_cost_graph = {
        "data": [
            go.Bar(
                x=x_date_series,
                y=cost_table_grouped_by_date[column].sum().reindex(x_date_series, fill_value=0),
                name=column,
                marker=dict(color=colors[i])  # Assign color here
            )
            for i, column in enumerate(["CPU COST", "GPU COST", "STORAGE COST"])
        ],
        "layout": go.Layout(
            title="Daily Costs by Type",
            barmode="stack",
            yaxis_tickprefix="$",
            yaxis_tickformat=",.",
        ),
    }
    return cumulative_cost_graph


def get_histogram_charts(cost_table: DataFrame) -> tuple:
    user_chart = build_histogram(cost_table, CostLabels.USER.value)
    project_chart = build_histogram(cost_table, CostLabels.PROJECT_NAME.value)
    org_chart = build_histogram(cost_table, CostLabels.ORGANIZATION.value)
    tag_chart = build_histogram(cost_table, CostLabels.BILLING_TAG.value)
    return user_chart, project_chart, org_chart, tag_chart


def workload_cost_details(cost_table: DataFrame) -> DataTable:
    formatted = {
        "locale": {},
        "nully": "",
        "prefix": None,
        "specifier": "$,.2f",
    }
    display_cost_col = True if cost_table[CostAggregatedLabels.CLOUD_COST.value].sum() > 0 else False
    logger.info("display cost details: display_cost_col %s", display_cost_col)

    columns = [
        {"name": "TYPE", "id": "TYPE"},
        {"name": CostLabels.PROJECT_NAME.value, "id": CostLabels.PROJECT_NAME.value},
        {"name": CostLabels.BILLING_TAG.value, "id": CostLabels.BILLING_TAG.value},
        {"name": CostLabels.USER.value, "id": CostLabels.USER.value},
        {"name": "START DATE", "id": "FORMATTED START"},
        {
            "name": CostFieldsLabels.CPU_COST.value,
            "id": CostFieldsLabels.CPU_COST.value,
            "type": "numeric",
            "format": formatted,
        },
        {
            "name": CostFieldsLabels.GPU_COST.value,
            "id": CostFieldsLabels.GPU_COST.value,
            "type": "numeric",
            "format": formatted,
        },
        {
            "name": CostFieldsLabels.STORAGE_COST.value,
            "id": CostFieldsLabels.STORAGE_COST.value,
            "type": "numeric",
            "format": formatted,
        },
    ]

    if display_cost_col:
        columns.append(
            {
                "name": CostAggregatedLabels.CLOUD_COST.value,
                "id": CostAggregatedLabels.CLOUD_COST.value,
                "type": "numeric",
                "format": formatted,
            }
        )

    columns.append(
        {
            "name": CostAggregatedLabels.TOTAL_COST.value,
            "id": CostAggregatedLabels.TOTAL_COST.value,
            "type": "numeric",
            "format": formatted,
        }
    )

    table = dash_table.DataTable(
        columns=columns,
        data=clean_df(cost_table, "TYPE").to_dict("records"),
        page_size=10,
        sort_action="native",
        style_cell={"fontSize": "11px"},
        style_header={"backgroundColor": "#e5ecf6", "fontWeight": "bold"},
    )
    return table


def get_dropdown_filters(cost_table: DataFrame) -> tuple:
    """
    First obtain a unique sorted list for each dropdown
    """
    users_list = clean_values(sorted(cost_table[CostLabels.USER.value].unique().tolist(), key=str.casefold))
    projects_list = clean_values(
        sorted(
            cost_table[CostLabels.PROJECT_NAME.value].unique().tolist(),
            key=str.casefold,
        )
    )

    unique_billing_tags = cost_table[CostLabels.BILLING_TAG.value].unique()
    unique_billing_tags = unique_billing_tags[unique_billing_tags != NO_TAG]
    billing_tags_list = sorted(unique_billing_tags.tolist(), key=str.casefold)
    billing_tags_list.insert(0, NO_TAG)

    # For each dropdown data return a dict containing the value/label/title (useful for tooltips)
    billing_tags_dropdown = [
        {"label": billing_tag, "value": billing_tag, "title": billing_tag} for billing_tag in billing_tags_list
    ]
    projects_dropdown = [{"label": project, "value": project, "title": project} for project in projects_list]
    users_dropdown = [{"label": user, "value": user, "title": user} for user in users_list]

    return billing_tags_dropdown, projects_dropdown, users_dropdown


def get_cost_cards(cost_table: DataFrame) -> tuple[str]:
    total_sum = "${:.2f}".format(cost_table[CostAggregatedLabels.TOTAL_COST.value].sum())
    cloud_sum = "${:.2f}".format(cost_table[CostAggregatedLabels.CLOUD_COST.value].sum())
    compute_sum = "${:.2f}".format(cost_table[CostAggregatedLabels.COMPUTE_COST.value].sum())
    storage_sum = "${:.2f}".format(cost_table[CostAggregatedLabels.STORAGE_COST.value].sum())
    return total_sum, cloud_sum, compute_sum, storage_sum


def build_histogram(cost_table: DataFrame, bin_by: str):
    top = clean_df(cost_table, bin_by).groupby(bin_by)[CostAggregatedLabels.TOTAL_COST.value].sum().nlargest(10).index
    costs = cost_table[cost_table[bin_by].isin(top)]
    data_index = costs.groupby(bin_by)[CostAggregatedLabels.TOTAL_COST.value].sum().sort_values(ascending=False).index
    title = "Top " + bin_by.title() + " by Total Cost"
    chart = px.histogram(
        costs,
        x=CostAggregatedLabels.TOTAL_COST.value,
        y=bin_by,
        orientation="h",
        title=title,
        labels={
            bin_by: bin_by.title(),
            CostAggregatedLabels.TOTAL_COST.value: "Total Cost",
        },
        hover_data={CostAggregatedLabels.TOTAL_COST.value: "$:.2f"},
        category_orders={bin_by: data_index},
        color_discrete_sequence=["#ff6543"]
    )
    chart.update_layout(
        title_text=title,
        title_x=0.5,
        xaxis_tickprefix="$",
        xaxis_tickformat=",.",
        yaxis={  # Trim labels that are larger than 15 chars
            "tickmode": "array",
            "tickvals": data_index,
            "ticktext": [
                f"{txt[:15]}..." if len(txt) > 15 else txt for txt in chart["layout"]["yaxis"]["categoryarray"]
            ],
        },
        dragmode=False,
    )
    chart.update_xaxes(title_text=CostAggregatedLabels.TOTAL_COST.value)
    chart.update_traces(hovertemplate="$%{x:.2f}<extra></extra>")

    return chart


def get_execution_cost_table(aggregated_allocations: List) -> list[dict]:
    exec_data = []

    for costData in aggregated_allocations:
        try:
            (
                workload_type,
                project_id,
                project_name,
                username,
                organization,
                billing_tag,
            ) = costData[
                "name"
            ].split("/")
        except Exception as e:
            print(e)
            continue

        cpu_cost = costData["cpuCost"] + costData["cpuCostAdjustment"]
        gpu_cost = costData["gpuCost"] + costData["gpuCostAdjustment"]
        compute_cost = cpu_cost + gpu_cost

        ram_cost = costData["ramCost"] + costData["ramCostAdjustment"]

        alloc_total_cost = costData["totalCost"]

        storage_cost = alloc_total_cost - compute_cost

        # Change __unallocated__ billing tag into "No Tag"
        billing_tag = billing_tag if billing_tag != "__unallocated__" else NO_TAG

        exec_data.append(
            {
                "TYPE": workload_type,
                CostLabels.PROJECT_NAME.value: project_name,
                CostLabels.BILLING_TAG.value: billing_tag,
                CostLabels.USER.value: username,
                CostLabels.ORGANIZATION.value: organization,
                "START": costData["window"]["start"],
                "END": costData["window"]["end"],
                CostFieldsLabels.CPU_COST.value: cpu_cost,
                CostFieldsLabels.GPU_COST.value: gpu_cost,
                CostFieldsLabels.COMPUTE_COST.value: compute_cost,
                CostFieldsLabels.MEMORY_COST.value: ram_cost,
                CostFieldsLabels.STORAGE_COST.value: storage_cost,
                CostFieldsLabels.ALLOC_COST.value: alloc_total_cost,
            }
        )

    return exec_data


def get_distributed_execution_cost(cost_table: list[dict[str, Any]], cloud_cost: float) -> DataFrame:
    execution_costs = distribute_cost(pd.DataFrame(cost_table))
    distributed_cost = distribute_cloud_cost(execution_costs, cloud_cost)

    distributed_cost["START"] = pd.to_datetime(distributed_cost["START"])
    distributed_cost["FORMATTED START"] = distributed_cost["START"].dt.strftime("%B %-d")

    return distributed_cost
