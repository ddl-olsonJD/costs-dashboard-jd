import os
import re
import requests
from datetime import timedelta
from typing import Callable, List

import dash_bootstrap_components as dbc
import pandas as pd
import plotly.express as px
import plotly.graph_objs as go
from dash import (
    Dash,
    dash_table,
    dcc,
    html
)
from dash.dependencies import Input, Output
from pandas import (
    DataFrame,
    Timestamp
)

from domino_cost.domino_cost import get_domino_namespace

api_host = os.environ["DOMINO_API_HOST"]
#
# def get_domino_namespace() -> str:
#     api_host = os.environ["DOMINO_API_HOST"]
#     pattern = re.compile("(https?://)((.*\.)*)(?P<ns>.*?):(\d*)\/?(.*)")
#     match = pattern.match(api_host)
#     return match.group("ns")

namespace = get_domino_namespace(api_host)

cost_url = f"http://domino-cost.{namespace}:9000"

api_proxy = os.environ["DOMINO_API_PROXY"]
auth_url = f"{api_proxy}/account/auth/service/authenticate"

class TokenExpiredException(Exception):
    pass

def get_token() -> str:
    orgs_res = requests.get(auth_url)
    token = orgs_res.content.decode('utf-8')
    if token == "<ANONYMOUS>":
        raise TokenExpiredException("Your token has expired. Please redeploy your Domino Cost App.")
    return token

auth_header = {
    'X-Authorization': get_token()
}

NO_TAG = "No tag"

window_to_param = {
    "30d": "Last 30 days",
    "14d": "Last 14 days",
    "7d": "Last 7 days"
}

def get_today_timestamp() -> Timestamp:
    return pd.Timestamp("today", tz="UTC").normalize()

def get_time_delta(time_span) -> timedelta:
        if time_span == 'lastweek':
            days_to_use = 7
        else:
            days_to_use = int(time_span[:-1])
        return timedelta(days=days_to_use-1)

def process_or_zero(func: Callable, posInt: int) -> int:
    if posInt > 0:
        return func
    else:
        return 0

def distribute_cost(df: DataFrame) -> DataFrame:
    """
    distributes __unallocated__ cost for cleaner representation.
    """
    fiels_list = [ "CPU COST",	"GPU COST",	"COMPUTE COST",	"MEMORY COST",	"STORAGE COST",	"ALLOC COST"]
    cost_unallocated = df[df["TYPE"].str.startswith("__")]
    cost_allocated = df[~df["TYPE"].str.startswith("__")]
    cost_allocated_total_sum = cost_allocated["ALLOC COST"].sum()

    for field in fiels_list:
        cost_allocated[field] = cost_allocated[field] + ((cost_allocated["ALLOC COST"] / cost_allocated_total_sum) * cost_unallocated[field].sum())
    return cost_allocated

def distribute_cloud_cost(df: DataFrame, cost: float) -> DataFrame:
    """
    distribute unaccounted cloud cost accross allocated executions.
    """
    accounted_cost = df["ALLOC COST"].sum()
    cloud_cost_unaccounted = process_or_zero(cost - accounted_cost, cost)
   
    df['CLOUD COST'] = cloud_cost_unaccounted * (df['ALLOC COST'] / accounted_cost)
    df['TOTAL COST'] = df['ALLOC COST'] + df['CLOUD COST']
    
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

def get_cloud_cost_sum(selection) -> float:
    cloud_cost_url = f"{cost_url}/cloudCost"

    parameters = {
        "window": selection,
        "aggregate": "invoiceEntityID"
    }

    cloud_cost_sum = 0

    try:
        response = requests.request("GET", cloud_cost_url, headers=auth_header, params=parameters)
        response.raise_for_status()
        
        cost_amortized = response.json()['data']['sets']
        invoice_entity_id = list(cost_amortized[0]['cloudCosts'].keys())[0]   
        
        for cost in cost_amortized:
            if cost['cloudCosts']:
                cloud_cost_sum += cost['cloudCosts'][invoice_entity_id]['amortizedNetCost']['cost']
    except Exception as e: # handle for users without cloudcost, or no data in cloudcost
        print(e)
        
    return cloud_cost_sum

def get_aggregated_allocations(selection: str) -> List:
    allocations_url = f"{cost_url}/allocation"

    params = {
        "window": selection,
        "aggregate": (
            "label:dominodatalab.com/workload-type,"
            "label:dominodatalab.com/project-id,"
            "label:dominodatalab.com/project-name,"
            "label:dominodatalab.com/starting-user-username,"
            "label:dominodatalab.com/organization-name,"
            "label:dominodatalab.com/billing-tag,"
        ),
        "accumulate": False,
        "shareIdle": True,
        "shareTenancyCosts": True,
        "shareSplit": "weighted",
    }

    res = requests.get(allocations_url, params=params, headers=auth_header)

    res.raise_for_status()
    alloc_data = res.json()["data"]

    return alloc_data

def get_execution_cost_table(aggregated_allocations: List, cloud_cost: float) -> DataFrame:

    exec_data = []

    storage_cost_keys = ["pvCost", "ramCost", "pvCostAdjustment", "ramCostAdjustment"]

    for costData in aggregated_allocations:

        workload_type, project_id, project_name, username, organization, billing_tag = costData["name"].split("/")
        
        cpu_cost = costData["cpuCost"] + costData["cpuCostAdjustment"]
        gpu_cost = costData["gpuCost"] + costData["gpuCostAdjustment"]
        compute_cost = cpu_cost + gpu_cost
        
        ram_cost = costData["ramCost"] + costData["ramCostAdjustment"]
        
        alloc_total_cost = costData["totalCost"]

        storage_cost = alloc_total_cost - compute_cost
        
        # Change __unallocated__ billing tag into "No Tag"
        billing_tag = billing_tag if billing_tag != '__unallocated__' else NO_TAG

        exec_data.append({
            "TYPE": workload_type,
            "PROJECT NAME": project_name,
            "BILLING TAG": billing_tag,
            "USER": username,
            "ORGANIZATION": organization,
            "START": costData["window"]["start"],
            "END": costData["window"]["end"],
            "CPU COST": cpu_cost,
            "GPU COST": gpu_cost,
            "COMPUTE COST": compute_cost,
            "MEMORY COST": ram_cost,
            "STORAGE COST": storage_cost,
            "ALLOC COST": alloc_total_cost
        })

    execution_costs = distribute_cost(pd.DataFrame(exec_data))
    distributed_cost = distribute_cloud_cost(execution_costs, cloud_cost)

    distributed_cost['START'] = pd.to_datetime(distributed_cost['START'])
    distributed_cost['FORMATTED START'] = distributed_cost['START'].dt.strftime('%B %-d')

    return distributed_cost

def get_cost_cards(cost_table: DataFrame) -> tuple[str]:
    total_sum = "${:.2f}".format(cost_table['TOTAL COST'].sum())
    cloud_sum = "${:.2f}".format(cost_table['CLOUD COST'].sum())
    compute_sum = "${:.2f}".format(cost_table['COMPUTE COST'].sum())
    storage_sum = "${:.2f}".format(cost_table['STORAGE COST'].sum())

    return total_sum, cloud_sum, compute_sum, storage_sum

def buildHistogram(cost_table: DataFrame, bin_by: str):
    top = clean_df(cost_table, bin_by).groupby(bin_by)['TOTAL COST'].sum().nlargest(10).index
    costs = cost_table[cost_table[bin_by].isin(top)]
    data_index = costs.groupby(bin_by)['TOTAL COST'].sum().sort_values(ascending=False).index
    title = "Top " + bin_by.title() + " by Total Cost"
    chart = px.histogram(costs, x='TOTAL COST', y=bin_by, orientation='h',
                              title=title, labels={bin_by: bin_by.title(), 'TOTAL COST': 'Total Cost'},
                              hover_data={'TOTAL COST': '$:.2f'},
                              category_orders={bin_by: data_index})
    chart.update_layout(
        title_text=title,
        title_x=0.5,
        xaxis_tickprefix = '$',
        xaxis_tickformat = ',.',
        yaxis={  # Trim labels that are larger than 15 chars
            'tickmode': 'array',
            'tickvals': data_index,
            'ticktext': [f"{txt[:15]}..." if len(txt) > 15 else txt for txt in chart['layout']['yaxis']['categoryarray']]
        },
        dragmode=False
    )
    chart.update_xaxes(title_text="Total Cost")
    chart.update_traces(hovertemplate='$%{x:.2f}<extra></extra>')

    return chart

requests_pathname_prefix = '/{}/{}/r/notebookSession/{}/'.format(
    os.environ.get("DOMINO_PROJECT_OWNER"),
    os.environ.get("DOMINO_PROJECT_NAME"),
    os.environ.get("DOMINO_RUN_ID")
)
app = Dash(__name__, external_stylesheets=[dbc.themes.BOOTSTRAP], routes_pathname_prefix=None, requests_pathname_prefix=requests_pathname_prefix)

app.layout = html.Div([
    html.H2('Domino Cost Management Report', style = {'textAlign': 'center', "margin-top": "30px"}),
    dbc.Row([
        dbc.Col(
            html.H4('Data select', style = {"margin-top": "20px"}),
            width=2
        ),
        dbc.Col(
            html.Hr(style = {"margin-top": "40px"}),
            width=10
        )
    ]),
    dbc.Row([
        dbc.Col(
            html.P("Time Span:", style={"float": "right", "margin-top": "5px"}),
            width=1
        ),
        dbc.Col(
            dcc.Dropdown(
                id='time_span_select',
                options = window_to_param,
                value = "30d",
                clearable = False,
                searchable = False
            ),
            width=2
        ),
        dbc.Col(width=9)
    ], style={"margin-top": "30px"}),
    dbc.Row([
        dbc.Col(
            html.H4('Filter data by', style = {"margin-top": "20px"}),
            width=2
        ),
        dbc.Col(
            html.Hr(style = {"margin-top": "40px"}),
            width=10
        )
    ], style={"margin-top": "50px"}),
    dbc.Row([
        dbc.Col(
            html.P("Billing Tag:", style={"float": "right", "margin-top": "5px"}),
            width=1
        ),
        dbc.Col(
            dcc.Dropdown(
                id='billing_select',
                options = ['No data'],
                clearable = True,
                searchable = True,
                style={"width": "100%", "whiteSpace":"nowrap"}
            ),
            width=3
        ),
        dbc.Col(
            html.P("Project:", style={"float": "right", "margin-top": "5px"}),
            width=1
        ),
        dbc.Col(
            dcc.Dropdown(
                id='project_select',
                options = ['No data'],
                clearable = True,
                searchable = True,
                style={"width": "100%", "whiteSpace":"nowrap"}
            ),
            width=3
        ),
        dbc.Col(
            html.P("User:", style={"float": "right", "margin-top": "5px"}),
            width=1
        ),
        dbc.Col(
            dcc.Dropdown(
                id='user_select',
                options = ['No data'],
                clearable = True,
                searchable = True,
                style={"width": "100%", "whiteSpace":"nowrap"}
            ),
            width=3
        ),
    ], style={"margin-top": "30px"}),
    dbc.Row([
        dbc.Col(dbc.Card(children=[
            dbc.CardBody([
                html.H3("Total"),
                html.H4("Loading", id='totalcard')
            ])
        ])),
        dbc.Col(dbc.Card(children=[
            dbc.CardBody([
                html.H3("Cloud"),
                html.H4("Loading", id='cloudcard')
            ])
        ])),
        dbc.Col(dbc.Card(children=[
            dbc.CardBody([
                html.H3("Compute"),
                html.H4("Loading", id='computecard')
            ])
        ])),
        dbc.Col(dbc.Card(children=[
            dbc.CardBody([
                html.H3("Storage"),
                html.H4("Loading", id='storagecard')
            ])
        ]))
    ], style={"margin-top": "50px"}),
    dcc.Loading(children=[
        dcc.Graph(
            id='cumulative-daily-costs',
            config = {
                'displayModeBar': False
            },
            style={'margin-top': '40px'}
        )
    ], type='default'),
    dbc.Row([
        dbc.Col(
            dcc.Graph(
                id='user_chart',
                config = {
                    'displayModeBar': False
                }
            )
        ),
        dbc.Col(
            dcc.Graph(
                id='project_chart',
                config = {
                    'displayModeBar': False
                }
            )
        )
    ]),
    dbc.Row([
        dbc.Col(
            dcc.Graph(
                id='org_chart',
                config = {
                    'displayModeBar': False
                }
            )
        ),
        dbc.Col(
            dcc.Graph(
                id='tag_chart',
                config = {
                    'displayModeBar': False
                }
            )
        )
    ]),
    html.H4('Workload Cost Details', style={"margin-top": "50px"}),
    html.Div(id='table-container'),
    dbc.Row([], style={"margin-top": "50px"}),
], className="container")

def get_dropdown_filters(cost_table: DataFrame) -> tuple:
    """ 
    First obtain a unique sorted list for each dropdown
    """
    users_list = clean_values(sorted(cost_table['USER'].unique().tolist(), key=str.casefold))
    projects_list = clean_values(sorted(cost_table['PROJECT NAME'].unique().tolist(), key=str.casefold))

    unique_billing_tags = cost_table['BILLING TAG'].unique()
    unique_billing_tags = unique_billing_tags[unique_billing_tags != NO_TAG]
    billing_tags_list = sorted(unique_billing_tags.tolist(), key=str.casefold)
    billing_tags_list.insert(0, NO_TAG)

    # For each dropdown data return a dict containing the value/label/title (useful for tooltips)
    billing_tags_dropdown = [{"label": billing_tag, "value": billing_tag, "title": billing_tag} for billing_tag in billing_tags_list]
    projects_dropdown = [{"label": project, "value": project, "title": project} for project in projects_list]
    users_dropdown = [{"label": user, "value": user, "title": user} for user in users_list]

    return billing_tags_dropdown, projects_dropdown, users_dropdown

def get_cumulative_cost_graph(cost_table: DataFrame, time_span: timedelta):
    x_date_series = pd.date_range(get_today_timestamp() - get_time_delta(time_span), get_today_timestamp()).strftime('%B %-d')
    cost_table_grouped_by_date = cost_table.groupby('FORMATTED START')

    cumulative_cost_graph = {
        'data': [
            go.Bar(
                x=x_date_series,
                y=cost_table_grouped_by_date[column].sum().reindex(x_date_series, fill_value=0),
                name=column
            ) for column in ['CPU COST', 'GPU COST', 'STORAGE COST']
        ],
        'layout': go.Layout(
            title='Daily Costs by Type',
            barmode='stack',
            yaxis_tickprefix = '$',
            yaxis_tickformat = ',.'
        )
    }
    return cumulative_cost_graph

def get_histogram_charts(cost_table: DataFrame):
    user_chart = buildHistogram(cost_table, 'USER')
    project_chart = buildHistogram(cost_table, 'PROJECT NAME')
    org_chart = buildHistogram(cost_table, 'ORGANIZATION')
    tag_chart = buildHistogram(cost_table, 'BILLING TAG')
    return user_chart, project_chart, org_chart, tag_chart

def workload_cost_details(cost_table: DataFrame):
    formatted = {'locale': {}, 'nully': '', 'prefix': None, 'specifier': '$,.2f'}
    table = dash_table.DataTable(
        columns=[
            {'name': "TYPE", 'id': "TYPE"},
            {'name': "PROJECT NAME", 'id': "PROJECT NAME"},
            {'name': "BILLING TAG", 'id': "BILLING TAG"},
            {'name': "USER", 'id': "USER"},
            {'name': "START DATE", 'id': "FORMATTED START"},
            {'name': "CPU COST", 'id': "CPU COST", 'type': 'numeric', 'format': formatted},
            {'name': "GPU COST", 'id': "GPU COST", 'type': 'numeric', 'format': formatted},
            {'name': "STORAGE COST", 'id': "STORAGE COST", 'type': 'numeric', 'format': formatted},
            {'name': "CLOUD COST", 'id': "CLOUD COST", 'type': 'numeric', 'format': formatted},
            {'name': "TOTAL COST", 'id': "TOTAL COST", 'type': 'numeric', 'format': formatted},
        ],
        data=clean_df(cost_table, "TYPE").to_dict('records'),
        page_size=10,
        sort_action='native',
        style_cell={'fontSize': '11px'},
        style_header={
            'backgroundColor': '#e5ecf6',
            'fontWeight': 'bold'
        }
    )
    return table

@app.callback(
    [
        Output('billing_select', 'options'),
        Output('project_select', 'options'),
        Output('user_select', 'options'),
        Output('totalcard', 'children'),
        Output('cloudcard', 'children'),
        Output('computecard', 'children'),
        Output('storagecard', 'children'),
        Output('cumulative-daily-costs', 'figure'),
        Output('user_chart', 'figure'),
        Output('project_chart', 'figure'),
        Output('org_chart', 'figure'),
        Output('tag_chart', 'figure'),
        Output('table-container', 'children')
    ],
    [
        Input('time_span_select', 'value'),
        Input('billing_select', 'value'),
        Input('project_select', 'value'),
        Input('user_select', 'value')
    ]
)
def update(time_span, billing_tag, project, user):
    cloud_cost_sum = get_cloud_cost_sum(time_span)
    allocations = get_aggregated_allocations(time_span)
    if not allocations:
        return [], [], [], 'No data', 'No data', 'No data', {}, None, None, None, None, None

    cost_table = get_execution_cost_table(allocations, cloud_cost_sum)

    if user is not None:
        cost_table = cost_table[cost_table['USER'] == user]

    if project is not None:
        cost_table = cost_table[cost_table['PROJECT NAME'] == project]

    if billing_tag is not None:
        cost_table = cost_table[cost_table['BILLING TAG'] == billing_tag]

    return (
        *get_dropdown_filters(cost_table),
        *get_cost_cards(cost_table),
        get_cumulative_cost_graph(cost_table, time_span),
        *get_histogram_charts(cost_table),
        workload_cost_details(cost_table),
    )


@app.callback(
    [Output('user_select', 'value')],
    [Input('user_chart', 'clickData')]
)
def user_clicked(clickData):
    if clickData is not None:
        x_value = clickData['points'][0]['y']
        return [x_value]
    else:
        return [None]

@app.callback(
    [Output('project_select', 'value')],
    [Input('project_chart', 'clickData')]
)
def project_clicked(clickData):
    if clickData is not None:
        x_value = clickData['points'][0]['y']
        return [x_value]
    else:
        return [None]

@app.callback(
    [Output('billing_select', 'value')],
    [Input('tag_chart', 'clickData')]
)
def billing_tag_clicked(clickData):
    if clickData is not None:
        x_value = clickData['points'][0]['y']
        return [x_value]
    else:
        return [None]

if __name__ == '__main__':
    app.run_server(host='0.0.0.0',port=8888) # Domino hosts all apps at 0.0.0.0:8888
