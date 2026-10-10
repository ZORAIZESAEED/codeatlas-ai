import type { NextFunction, Request, Response } from "express";
import jwt from "jsonwebtoken";
import { config } from "../config";

export interface AuthedRequest extends Request {
  userId?: string;
}

export function requireAuth(req: AuthedRequest, res: Response, next: NextFunction) {
  const header = req.headers.authorization ?? "";
  const [scheme, token] = header.split(" ");
  if (scheme !== "Bearer" || !token) {
    return res.status(401).json({ error: "Missing bearer token" });
  }
  try {
    const claims = jwt.verify(token, config.jwtSecret) as { sub: string };
    req.userId = claims.sub;
    next();
  } catch {
    res.status(401).json({ error: "Invalid or expired token" });
  }
}
