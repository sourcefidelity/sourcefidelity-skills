# Glossary

**Admission**: the single decision that acquired bytes are the cited work, complete
enough and readable, followed by canonical staging and registration. Admission is
the public seam; format handling is an implementation detail.

**Admission record**: one versioned record per candidate, accepted or rejected. It
carries provenance, verdicts, source hash, staged path, evidence eligibility,
reason code and admission policy version.

**Complete admission**: an admission record with high identity confidence, complete
coverage and readable text. Only complete admissions are `evidence_eligible`.

**Identity-only admission**: the right work, but incomplete, scan-only or otherwise
not evidence-ready. The copy is staged for provenance and limitation reporting, not
for extraction or judgment.

**Evidence eligible**: the stored decision that a representation may support
extraction and judgment. Consumers read this field; they do not re-derive it from
completeness or quality.

**Selected representation**: the one representation preparation uses for a source.
Selection prefers evidence-eligible copies, then supplied → acquired → web, then
digital over scan.

**Admitted confirmed / admitted identity only**: registry identity statuses for a
source with at least one complete admitted copy, or only identity-only admitted
copies respectively. Supplied and manual mappings keep their existing statuses.

**Registry ingest adapter**: the thin persistence adapter that writes an admission
record into the source registry without translating its verdicts.

**Copy admissibility**: the single policy that answers whether one observed copy
may establish an affirmative judgment, a negative/absence conclusion, pagination,
a recovery window or a reappraisal change. Consumers pass the use and the copy
observation; they do not match raw scope or edition statuses.

**Bounded request**: one claim's model-facing payload plus its digest binding.
Every emitter writes the same envelope; ordinary assessment and full-text recovery
differ only in packet fields and verdict policy.

**Artifact transaction**: the one commit path for paper artifacts. A caller names
the paths it may touch and a mutation; the transaction snapshots, mutates,
verifies and restores every path on failure.
