import cors from "cors";
import express from "express";
import { config } from "./config";
import { connectDatabase } from "./db";
import { requireAuth } from "./middleware/auth";
import tasksRouter from "./routes/tasks";
import usersRouter from "./routes/users";

const app = express();
app.use(cors({ origin: config.corsOrigins }));
app.use(express.json({ limit: "100kb" }));

app.use("/api", usersRouter);
app.use("/api/tasks", requireAuth, tasksRouter);

connectDatabase(config.mongoUrl).then(() => {
  app.listen(config.port, () => {
    console.log(`Taskboard API listening on port ${config.port}`);
  });
});
