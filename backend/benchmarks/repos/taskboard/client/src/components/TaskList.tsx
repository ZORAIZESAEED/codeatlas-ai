import { useEffect, useState } from "react";
import { apiFetch } from "../api/client";
import { formatDueDate, isOverdue } from "../utils/date";

type Task = { id: string; title: string; dueDate?: string; done: boolean };

export function TaskList() {
  const [tasks, setTasks] = useState<Task[]>([]);

  useEffect(() => {
    apiFetch<Task[]>("/tasks").then(setTasks);
  }, []);

  async function toggle(task: Task) {
    const updated = await apiFetch<Task>(`/tasks/${task.id}/complete`, { method: "PATCH" });
    setTasks((current) => current.map((t) => (t.id === task.id ? updated : t)));
  }

  return (
    <ul className="task-list">
      {tasks.map((task) => (
        <li key={task.id} className={isOverdue(task.dueDate) && !task.done ? "overdue" : ""}>
          <input type="checkbox" checked={task.done} onChange={() => toggle(task)} />
          {task.title}
          {task.dueDate && <span className="due">{formatDueDate(task.dueDate)}</span>}
        </li>
      ))}
    </ul>
  );
}
