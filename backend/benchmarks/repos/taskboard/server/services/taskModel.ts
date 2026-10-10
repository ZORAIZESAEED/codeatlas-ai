import { Schema, model } from "mongoose";

export interface Task {
  id: string;
  owner: string;
  title: string;
  dueDate?: string;
  done: boolean;
  completedAt?: Date;
}

const taskSchema = new Schema<Task>({
  owner: { type: String, required: true, index: true },
  title: { type: String, required: true },
  dueDate: String,
  done: { type: Boolean, default: false },
  completedAt: Date,
});

export const TaskModel = model<Task>("Task", taskSchema);
