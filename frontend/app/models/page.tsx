"use client"

import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts"
import { DashboardLayout } from "@/components/dashboard/dashboard-layout"
import { ComponentTag, ErrorLine, PageHeader, Panel } from "@/components/dashboard/fleet-ui"
import { usePoll } from "@/hooks/use-poll"
import { api } from "@/lib/api"

interface ModelRow {
  component: string; served_model: string; prevalence: number; pr_auc_logreg: number; pr_auc_gbm: number
  served: { pr_auc: number; roc_auc: number; brier: number; precision: number; recall: number; "precision_at_top_1%": number }
  holdout_pr_auc: number | null; features_used: number; rows: Record<string, number>; top_features: { feature: string; pr_auc_drop: number }[]
}

function ModelsInner() {
  const { data, error } = usePoll(() => api<{ model_version: string; dataset_version: string; feature_schema_version: string; components: ModelRow[] }>("/models"), 0)
  const rows = data?.components || []
  const chart = rows.map((r) => ({ component: r.component, "Chance (prevalence)": r.prevalence, "Logistic regression": r.pr_auc_logreg, "Gradient boosting": r.pr_auc_gbm }))

  return (
    <div className="p-4 md:p-6 lg:p-8">
      <PageHeader eyebrow={data ? `model ${data.model_version} · dataset ${data.dataset_version} · features ${data.feature_schema_version}` : "models"}
        title="Model Performance"
        subtitle="Evaluated once on a held-out, later time window (chronological split, never random). PR-AUC is the headline metric because failures are rare." />
      <ErrorLine error={error} />
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4 md:gap-6 mb-6">
        <Panel title="PR-AUC on the final test window vs chance">
          <div className="h-72">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={chart}>
                <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
                <XAxis dataKey="component" tick={{ fill: "var(--muted-foreground)", fontSize: 11 }} />
                <YAxis domain={[0, 1]} tick={{ fill: "var(--muted-foreground)", fontSize: 11 }} />
                <Tooltip contentStyle={{ background: "var(--card)", border: "1px solid var(--border)", fontSize: 12 }} formatter={(v: number) => v.toFixed(3)} />
                <Legend wrapperStyle={{ fontSize: 11 }} />
                <Bar dataKey="Chance (prevalence)" fill="var(--muted-foreground)" />
                <Bar dataKey="Logistic regression" fill="var(--chart-2)" />
                <Bar dataKey="Gradient boosting" fill="var(--lime)" />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </Panel>
        <Panel title="How the numbers are produced">
          <ul className="text-sm text-muted-foreground space-y-2 list-disc pl-4">
            <li>Label: a maintenance or failure event in the next 7 days. Snapshots while awaiting service, or whose horizon is unobserved, are excluded.</li>
            <li>Features use only data before the prediction time; a leakage test deletes and perturbs all later data and checks nothing changes.</li>
            <li>Splits by time: train (days 1-52), calibration (60-64), threshold selection (65-68), test (76-83), with 7-day buffers.</li>
            <li>Probabilities are Platt-calibrated, so expected losses add up across the fleet. The served model is chosen on the threshold window, never on test.</li>
          </ul>
        </Panel>
      </div>
      <Panel title="Served models on the test window">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead><tr className="text-left text-xs text-muted-foreground font-mono uppercase tracking-wider">
              <th className="py-2 pr-3">Component</th><th className="py-2 pr-3">Served model</th><th className="py-2 pr-3">PR-AUC</th><th className="py-2 pr-3">x chance</th><th className="py-2 pr-3">ROC-AUC</th><th className="py-2 pr-3">Brier</th><th className="py-2 pr-3">Precision @ top 1%</th><th className="py-2 pr-3">Holdout PR-AUC</th><th className="py-2">Top signals (GBM)</th>
            </tr></thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.component} className="border-t border-border align-top">
                  <td className="py-2 pr-3"><ComponentTag component={r.component} /></td>
                  <td className="py-2 pr-3">{r.served_model}</td>
                  <td className="py-2 pr-3 font-mono">{r.served.pr_auc.toFixed(3)}</td>
                  <td className="py-2 pr-3 font-mono text-lime">{(r.served.pr_auc / r.prevalence).toFixed(1)}x</td>
                  <td className="py-2 pr-3 font-mono">{r.served.roc_auc.toFixed(3)}</td>
                  <td className="py-2 pr-3 font-mono">{r.served.brier.toFixed(4)}</td>
                  <td className="py-2 pr-3 font-mono">{(100 * r.served["precision_at_top_1%"]).toFixed(0)}%</td>
                  <td className="py-2 pr-3 font-mono">{r.holdout_pr_auc?.toFixed(3) ?? "-"}</td>
                  <td className="py-2 text-xs font-mono text-muted-foreground">{r.top_features.map((f) => f.feature.replace(/^f_/, "")).join(", ") || "-"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="text-[11px] text-muted-foreground mt-3">Brake risk is only weakly predictable in the simulation (its wear shows mostly during rare harsh braking); this is reported, not hidden.</p>
      </Panel>
    </div>
  )
}

export default function Page() { return <DashboardLayout><ModelsInner /></DashboardLayout> }
