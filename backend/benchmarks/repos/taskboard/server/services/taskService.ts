import { TaskModel, type Task } from "./taskModel";

export class TaskService {
  async listTasksForUser(userId: string): Promise<Task[]> {
    return TaskModel.find({ owner: userId }).sort({ dueDate: 1 });
  }

  async createTask(userId: string, title: string, dueDate?: string): Promise<Task> {
    return TaskModel.create({ owner: userId, title, dueDate, done: false });
  }

  async completeTask(userId: string, taskId: string): Promise<Task | null> {
    return TaskModel.findOneAndUpdate(
      { _id: taskId, owner: userId },
      { done: true, completedAt: new Date() },
      { new: true },
    );
  }

  async deleteTask(userId: string, taskId: string): Promise<boolean> {
    const result = await TaskModel.deleteOne({ _id: taskId, owner: userId });
    return result.deletedCount === 1;
  }
}
