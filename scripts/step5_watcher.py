# -*- coding: utf-8 -*-
"""Step4→Step5 自动衔接守望器（纯轮询，零 AI 介入）。

轮询 run_logs：step4 出现 RUN_END 且尚无 step5 记录 → 登录引擎触发 Step5。
仅此一件事；服务不可达则退出并留痕。
"""
import io
import json
import time
import urllib.request
import http.cookiejar

import pymysql

import os
DB = dict(host="127.0.0.1", user="root", password=os.getenv("STORAGE_MYSQL_PASSWORD", ""),
          database="ai_test_engine", charset="utf8mb4")
ENGINE = "http://127.0.0.1:5009"
PROJECT = "fullcov_sut"
LOG = r"D:\AI\AI_Test_Preject\logs\step5_watcher.log"


def log(msg: str) -> None:
    line = "[watcher %s] %s" % (time.strftime("%H:%M:%S"), msg)
    print(line, flush=True)
    with io.open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def q(sql: str):
    conn = pymysql.connect(**DB)
    cur = conn.cursor()
    cur.execute(sql)
    rows = cur.fetchall()
    conn.close()
    return rows


def main() -> None:
    log("watcher started")
    triggered = False
    while True:
        try:
            s4 = q("SELECT COUNT(*) FROM run_logs WHERE run_id='fullcov_sut__v1_0' "
                   "AND step_num=4 AND message LIKE '%%RUN_END%%'")[0][0]
            s5 = q("SELECT COUNT(*) FROM run_logs WHERE run_id='fullcov_sut__v1_0' "
                   "AND step_num=5")[0][0]
            if s4 and not s5 and not triggered:
                log("Step4 RUN_END detected -> triggering Step5")
                cj = http.cookiejar.CookieJar()
                op = urllib.request.build_opener(
                    urllib.request.HTTPCookieProcessor(cj))
                op.open(urllib.request.Request(
                    ENGINE + "/api/login",
                    data=json.dumps({"user_id": "admin", "password": "admin123"}).encode(),
                    headers={"Content-Type": "application/json"}, method="POST"), timeout=20)
                op.open(urllib.request.Request(
                    ENGINE + "/api/project",
                    data=json.dumps({"name": PROJECT}).encode(),
                    headers={"Content-Type": "application/json"}, method="POST"), timeout=20)
                r = op.open(urllib.request.Request(
                    ENGINE + "/api/step/5",
                    data=json.dumps({"project": PROJECT, "step": 5}).encode(),
                    headers={"Content-Type": "application/json"}, method="POST"), timeout=30)
                log("step5 trigger: %s %s" % (r.status, r.read()[:120]))
                triggered = True
                # Step5 也要跑一段时间；跑完（step5 RUN_END）后退出
            if s5 and triggered:
                s5end = q("SELECT COUNT(*) FROM run_logs WHERE run_id='fullcov_sut__v1_0' "
                          "AND step_num=5 AND message LIKE '%%RUN_END%%'")[0][0]
                if s5end:
                    log("Step5 RUN_END detected -> watcher done")
                    return
        except Exception as e:  # noqa: BLE001
            log("err: %r" % (e,))
        time.sleep(60)


if __name__ == "__main__":
    main()
