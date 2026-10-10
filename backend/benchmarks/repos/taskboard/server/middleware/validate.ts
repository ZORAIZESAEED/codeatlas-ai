import type { NextFunction, Request, Response } from "express";

export type FieldRule = { required?: boolean; type: "string" | "number" | "boolean"; maxLength?: number };
export type Schema = Record<string, FieldRule>;

export function validateBody(schema: Schema) {
  return (req: Request, res: Response, next: NextFunction) => {
    const errors: string[] = [];
    for (const [field, rule] of Object.entries(schema)) {
      const value = req.body?.[field];
      if (value === undefined) {
        if (rule.required) errors.push(`${field} is required`);
        continue;
      }
      if (typeof value !== rule.type) errors.push(`${field} must be a ${rule.type}`);
      if (rule.maxLength && typeof value === "string" && value.length > rule.maxLength) {
        errors.push(`${field} is too long`);
      }
    }
    if (errors.length) return res.status(400).json({ errors });
    next();
  };
}
