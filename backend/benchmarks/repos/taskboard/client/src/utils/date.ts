const formatter = new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric" });

export function formatDueDate(iso: string): string {
  return formatter.format(new Date(iso));
}

export function isOverdue(iso?: string, now: Date = new Date()): boolean {
  if (!iso) return false;
  const due = new Date(iso);
  due.setHours(23, 59, 59, 999);
  return due.getTime() < now.getTime();
}
