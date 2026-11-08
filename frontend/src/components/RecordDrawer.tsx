import type { ReactNode } from "react";
import { Eye } from "lucide-react";
import { FormDrawer } from "./FormDrawer";
import { ResourceStatus } from "./WorkspacePage";

export type RecordDetails = {
  title: string;
  eyebrow?: string;
  description?: string;
  status?: string | null;
  sections: Array<{
    title: string;
    fields: Array<{ label: string; value: ReactNode; mono?: boolean }>;
  }>;
};

export function ViewAction({ onClick, label }: { onClick: () => void; label: string }) {
  return <button type="button" className="record-view-action" onClick={onClick} aria-label={`View ${label}`}><Eye size={14} aria-hidden="true" /> View</button>;
}

export function RecordDrawer({ record, onClose, children }: { record: RecordDetails; onClose: () => void; children?: ReactNode }) {
  return <FormDrawer title={record.title} description={record.description} eyebrow={record.eyebrow || "RECORD DETAILS"} onClose={onClose} readOnly className="record-details-drawer">
    {record.status && <div className="record-state-summary"><ResourceStatus value={record.status} /><span>Workspace record details</span></div>}
    {record.sections.map((section) => <section className="record-detail-section" key={section.title}><h3>{section.title}</h3><dl>{section.fields.map((field) => <div className="record-detail-field" key={field.label}><dt>{field.label}</dt><dd className={field.mono ? "resource-mono" : undefined}>{field.value === null || field.value === undefined || field.value === "" ? "—" : field.value === true ? "Yes" : field.value === false ? "No" : field.value}</dd></div>)}</dl></section>)}
    {children}
  </FormDrawer>;
}
