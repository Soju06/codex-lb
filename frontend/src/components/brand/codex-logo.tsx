import { cn } from "@/lib/utils";

export type CodexLogoProps = {
  className?: string;
  size?: number;
};

export function CodexLogo({ className, size = 32 }: CodexLogoProps) {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      width={size}
      height={size}
      fill="none"
      viewBox="0 0 32 32"
      className={cn("shrink-0", className)}
    >
      <path
        stroke="currentColor"
        strokeLinecap="round"
        strokeWidth="2.484"
        d="M22.356 19.797H17.17M9.662 12.29l1.979 3.576a.511.511 0 0 1-.005.504l-1.974 3.409M30.758 16c0 8.15-6.607 14.758-14.758 14.758-8.15 0-14.758-6.607-14.758-14.758C1.242 7.85 7.85 1.242 16 1.242c8.15 0 14.758 6.608 14.758 14.758Z"
      />
    </svg>
  );
}
