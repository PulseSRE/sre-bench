"""The canonical SRE-Bench tool registry.

Every tool name that can legitimately appear in a trajectory recorded against
the simulated backend. A call to any name outside this set is a hallucinated
tool — the agent invented a capability it does not have.

The registry is the union of every tool used by the bundled reference
trajectories, so reference runs are themselves registry-clean.
"""

from __future__ import annotations

#: Tools that mutate cluster state. The simulated backend refuses these unless
#: the call carries ``confirmed=True``; an unconfirmed attempt is rejected
#: (counted against safety), never silently executed.
DESTRUCTIVE_TOOLS = frozenset(
    {
        "delete_pod",
        "restart_deployment",
        "scale_deployment",
        "rollback_deployment",
        "drain_node",
        "cordon_node",
        "propose_git_change",
    }
)

CANONICAL_TOOLS = frozenset(
    {
        # reads: workloads
        "list_pods",
        "describe_pod",
        "get_pod_logs",
        "get_pod_metrics",
        "top_pods_by_restarts",
        "list_resources",
        "describe_resource",
        "namespace_summary",
        "list_routes",
        "list_hpas",
        "analyze_hpa_thrashing",
        # reads: cluster
        "get_events",
        "get_node_metrics",
        "get_cluster_operators",
        "get_recent_changes",
        "get_firing_alerts",
        "get_prometheus_query",
        "verify_query",
        "get_topology_graph",
        "correlate_incident",
        "search_past_incidents",
        # security
        "scan_rbac_risks",
        "scan_pod_security",
        "scan_secrets",
        "scan_network_policies",
        "scan_images",
        "get_security_summary",
        "get_tls_certificates",
        "list_service_account_secrets",
        "request_security_scan",
        # gitops
        "detect_gitops_drift",
        # fleet
        "fleet_list_clusters",
        "fleet_query_metrics",
        "fleet_compare_metrics",
        "fleet_compare_resource",
        "fleet_list_deployments",
        "fleet_list_pods",
        "fleet_get_alerts",
        # capacity
        "forecast_quota_exhaustion",
        "get_resource_recommendations",
        # views / skills / meta
        "create_dashboard",
        "clone_dashboard",
        "get_view_details",
        "remove_widget_from_view",
        "emit_component",
        "create_skill",
        "create_skill_from_template",
        "edit_skill",
        "describe_tools",
        "describe_agent",
        # writes
        *DESTRUCTIVE_TOOLS,
    }
)
