/** Suggest an editable identifier within the API's length and character limits. */
export function slugFromName(value: string, maxLength: number) {
  return value.trim().toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, maxLength).replace(/-+$/g, "");
}
