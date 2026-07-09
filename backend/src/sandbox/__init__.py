"""Sandbox Executor 占位：B8 块落地（09 号方案，2026-07-09 需求变更后口径）。

per-user Docker 容器会话期常驻（run 开启建、末 run 关闭后 TTL 回收）；所有 Skill 脚本
（平台+用户）与容器内受控 Bash 均在该用户容器内执行，底座复用 agentscope
DockerWorkspaceManager / DockerWorkspace / Bash + PermissionEngine。
"""
