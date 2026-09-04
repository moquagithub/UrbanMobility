import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

/** Fusionne des classes Tailwind conditionnelles sans conflits (pattern shadcn/ui standard). */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}
