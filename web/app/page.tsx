"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";

import { useAuth } from "@/lib/auth";

export default function Home() {
  const { me, loading } = useAuth();
  const router = useRouter();
  useEffect(() => {
    if (!loading) router.replace(me ? (me.is_platform_admin ? "/admin" : "/cabinet") : "/login");
  }, [loading, me, router]);
  return null;
}
