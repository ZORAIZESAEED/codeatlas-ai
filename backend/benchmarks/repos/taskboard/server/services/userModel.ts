import { Schema, model } from "mongoose";

export interface User {
  id: string;
  email: string;
  passwordHash: string;
}

const userSchema = new Schema<User>({
  email: { type: String, required: true, unique: true, lowercase: true },
  passwordHash: { type: String, required: true },
});

export const UserModel = model<User>("User", userSchema);
