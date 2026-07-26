import * as React from "react";
import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

const buttonVariants = cva(
  "ui-frost-button inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-xl text-sm font-medium ring-offset-background transition-all duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:pointer-events-none disabled:opacity-50",
  {
    variants: {
      variant: {
        default: "border border-primary/40 bg-primary text-primary-foreground shadow-[0_10px_28px_hsl(var(--primary)/0.2),inset_0_1px_0_rgba(255,255,255,0.28)] hover:-translate-y-0.5 hover:bg-primary/90 hover:shadow-[0_14px_34px_hsl(var(--primary)/0.28),inset_0_1px_0_rgba(255,255,255,0.34)]",
        destructive: "border border-destructive/50 bg-destructive text-destructive-foreground shadow-[0_10px_26px_hsl(var(--destructive)/0.2),inset_0_1px_0_rgba(255,255,255,0.24)] hover:-translate-y-0.5 hover:bg-destructive/90",
        outline: "border border-input/80 bg-background/65 shadow-[0_8px_24px_rgba(2,8,23,0.08),inset_0_1px_0_rgba(255,255,255,0.15)] backdrop-blur-xl hover:-translate-y-0.5 hover:border-primary/40 hover:bg-accent/80 hover:text-accent-foreground",
        secondary: "border border-border/70 bg-secondary/75 text-secondary-foreground shadow-[0_8px_24px_rgba(2,8,23,0.08),inset_0_1px_0_rgba(255,255,255,0.14)] backdrop-blur-xl hover:-translate-y-0.5 hover:bg-secondary",
        ghost: "border border-transparent hover:border-border/60 hover:bg-accent/65 hover:text-accent-foreground hover:shadow-[0_8px_22px_rgba(2,8,23,0.08)]",
        link: "text-primary underline-offset-4 hover:underline",
        gradient: "border border-cyan-200/35 bg-gradient-to-r from-cyan-400 via-sky-500 to-indigo-500 text-white shadow-[0_14px_34px_rgba(14,165,233,0.28),inset_0_1px_0_rgba(255,255,255,0.35)] hover:-translate-y-0.5 hover:from-cyan-300 hover:via-sky-400 hover:to-indigo-400",
      },
      size: {
        default: "h-10 px-4 py-2",
        sm: "h-9 rounded-md px-3",
        lg: "h-11 rounded-md px-8",
        icon: "h-10 w-10",
      },
    },
    defaultVariants: {
      variant: "default",
      size: "default",
    },
  }
);

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  asChild?: boolean;
}

const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild = false, ...props }, ref) => {
    const Comp = asChild ? Slot : "button";
    return (
      <Comp
        className={cn(buttonVariants({ variant, size, className }))}
        ref={ref}
        {...props}
      />
    );
  }
);
Button.displayName = "Button";

export { Button, buttonVariants };
