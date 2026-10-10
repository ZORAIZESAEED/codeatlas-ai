import { Router } from "express";
import type { AuthedRequest } from "../middleware/auth";
import { validateBody } from "../middleware/validate";
import { TaskService } from "../services/taskService";

const router = Router();
const tasks = new TaskService();

router.get("/", async (req: AuthedRequest, res) => {
  res.json(await tasks.listTasksForUser(req.userId!));
});

router.post(
  "/",
  validateBody({ title: { required: true, type: "string", maxLength: 200 }, dueDate: { type: "string" } }),
  async (req: AuthedRequest, res) => {
    const task = await tasks.createTask(req.userId!, req.body.title, req.body.dueDate);
    res.status(201).json(task);
  },
);

router.patch("/:id/complete", async (req: AuthedRequest, res) => {
  res.json(await tasks.completeTask(req.userId!, req.params.id));
});

router.delete("/:id", async (req: AuthedRequest, res) => {
  const removed = await tasks.deleteTask(req.userId!, req.params.id);
  if (!removed) return res.status(404).json({ error: "Task not found" });
  res.status(204).end();
});

export default router;
