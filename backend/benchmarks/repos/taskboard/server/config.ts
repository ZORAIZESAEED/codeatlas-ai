export interface ServerConfig {
  port: number;
  mongoUrl: string;
  jwtSecret: string;
  tokenTtlSeconds: number;
  corsOrigins: string[];
}

export const config: ServerConfig = {
  port: Number(process.env.PORT ?? 4000),
  mongoUrl: process.env.MONGO_URL ?? "mongodb://localhost:27017/taskboard",
  jwtSecret: process.env.JWT_SECRET ?? "dev-secret",
  tokenTtlSeconds: 60 * 60 * 8,
  corsOrigins: (process.env.CORS_ORIGINS ?? "http://localhost:5173").split(","),
};
