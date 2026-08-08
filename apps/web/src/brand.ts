/**
 * Single source of truth for user-facing product naming.
 *
 * Internal identifiers that are not user-facing — the `forge_refresh` cookie,
 * the `forge2026` demo password, the `forge/` Python package — are left alone
 * on purpose; renaming them touches auth plumbing for no visible benefit.
 */
export const PRODUCT_NAME = "DefectSense";
export const PRODUCT_DESCRIPTION =
  "AI-powered manufacturing quality control defect detection";
export const PRODUCT_LINE = "Wheel Assembly QC";

// The in-app chat/voice assistant's given name -- read-only, explains
// inspections, never submits or overrides one (see components/Assistant.tsx).
export const ASSISTANT_NAME = "Divya";
