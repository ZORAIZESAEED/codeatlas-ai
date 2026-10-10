import bcrypt from "bcryptjs";
import { Router } from "express";
import jwt from "jsonwebtoken";
import { config } from "../config";
import { validateBody } from "../middleware/validate";
import { UserModel } from "../services/userModel";

const router = Router();

export function signToken(userId: string): string {
  return jwt.sign({ sub: userId }, config.jwtSecret, { expiresIn: config.tokenTtlSeconds });
}

const credentials = {
  email: { required: true, type: "string" as const, maxLength: 254 },
  password: { required: true, type: "string" as const },
};

router.post("/signup", validateBody(credentials), async (req, res) => {
  const existing = await UserModel.findOne({ email: req.body.email });
  if (existing) return res.status(409).json({ error: "Email already registered" });
  const passwordHash = await bcrypt.hash(req.body.password, 12);
  const user = await UserModel.create({ email: req.body.email, passwordHash });
  res.status(201).json({ token: signToken(user.id) });
});

router.post("/login", validateBody(credentials), async (req, res) => {
  const user = await UserModel.findOne({ email: req.body.email });
  const ok = user && (await bcrypt.compare(req.body.password, user.passwordHash));
  if (!ok) return res.status(401).json({ error: "Invalid email or password" });
  res.json({ token: signToken(user.id) });
});

export default router;
