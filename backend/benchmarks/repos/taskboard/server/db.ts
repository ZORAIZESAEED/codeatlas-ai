import mongoose from "mongoose";

const MAX_ATTEMPTS = 5;

export async function connectDatabase(url: string): Promise<void> {
  for (let attempt = 1; attempt <= MAX_ATTEMPTS; attempt++) {
    try {
      await mongoose.connect(url);
      return;
    } catch (error) {
      const delay = 500 * 2 ** attempt;
      console.warn(`MongoDB connection failed (attempt ${attempt}), retrying in ${delay} ms`);
      await new Promise((resolve) => setTimeout(resolve, delay));
    }
  }
  throw new Error("Could not connect to MongoDB");
}
