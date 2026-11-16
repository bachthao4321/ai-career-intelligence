import asyncio
import re
from dataclasses import asdict
from typing import Any, Literal, TypedDict

from langgraph.graph import END, START, StateGraph
from sqlalchemy.exc import SQLAlchemyError

from app.core.security.sql import UnsafeSQL
from app.schemas.ask import AskRequest, AskResponse
from app.schemas.search import SearchFilters, SearchRequest
from app.services.ask.analytics import AnalyticsExecutor, AnalyticsUnavailable
from app.services.ask.planner import Plan, Planner, deterministic_plan
from app.services.search.documents import CorpusTooLarge, load_documents
from app.services.search.service import SearchService


class AgentState(TypedDict, total=False):
    request: AskRequest
    plan: Plan
    planner: Literal["deterministic", "model"]
    response: AskResponse


class CareerAgent:
    def __init__(self, search: SearchService, analytics: AnalyticsExecutor, planner: Planner):
        self.search, self.analytics, self.planner = search, analytics, planner
        graph = StateGraph(AgentState)
        graph.add_node("route", self.route)
        graph.add_node("search", self.search_node)
        graph.add_node("analytics", self.analytics_node)
        graph.add_node("clarify", self.clarify_node)
        graph.add_edge(START, "route")
        graph.add_conditional_edges(
            "route",
            lambda s: s["plan"].route,
            {"search": "search", "analytics": "analytics", "clarify": "clarify"},
        )
        for node in ("search", "analytics", "clarify"):
            graph.add_edge(node, END)
        self.graph = graph.compile()

    async def ask(self, request: AskRequest) -> AskResponse:
        state = await self.graph.ainvoke({"request": request}, config={"recursion_limit": 5})
        return state["response"]  # type: ignore[no-any-return]

    async def route(self, state: AgentState) -> AgentState:
        plan = await asyncio.to_thread(deterministic_plan, state["request"].question)
        if plan:
            return {"plan": plan, "planner": "deterministic"}
        return {"plan": await self.planner.plan(state["request"].question), "planner": "model"}

    def response(self, state: AgentState, **kwargs: Any) -> AskResponse:
        return AskResponse(
            route=state["plan"].route,
            planner=state["planner"],
            scope="local_research" if self.analytics.scope == "local" else "public_sources",
            **kwargs,
        )

    async def search_node(self, state: AgentState) -> AgentState:
        request = state["request"]
        result = await self.search.search(
            SearchRequest(query=request.question, mode=request.search_mode, top_k=request.top_k)
        )
        return {
            "response": self.response(
                state,
                status="ok",
                search=result,
                answer=f"Trả về {len(result.items)} kết quả từ corpus "
                f"{result.unique_documents} nhóm nội dung đủ điều kiện.",
                warnings=result.warnings,
            )
        }

    async def analytics_node(self, state: AgentState) -> AgentState:
        try:
            result = await self.analytics.execute(state["plan"].sql or "")
        except UnsafeSQL:
            return {
                "response": self.response(
                    state,
                    status="rejected",
                    answer="SQL ngoài phạm vi được phép. Hãy hỏi một thống kê đơn giản.",
                    warnings=["sql_validation_rejected"],
                )
            }
        except AnalyticsUnavailable as exc:
            return {
                "response": self.response(
                    state,
                    status="unavailable",
                    answer="Analytics chưa sẵn sàng hoặc truy vấn đã vượt giới hạn thời gian.",
                    warnings=[str(exc)],
                )
            }
        warnings = [
            "sample_not_market_estimate",
            "skills_are_literal_mentions",
            "counts_are_source_listings_unless_distinct_content_hash",
        ]
        if result.row_limit_reached and state["plan"].reason != "cloud_data_engineer":
            warnings.append("row_limit_reached_more_rows_may_exist")
        if result.values_truncated:
            warnings.append("long_values_truncated")
        evidence: list[dict[str, Any]] = []
        plan = state["plan"]
        if state["planner"] == "deterministic" and (
            plan.reason.startswith("skill_count:") or plan.reason == "cloud_data_engineer"
        ):
            slugs = (
                [plan.reason.split(":", 1)[1]]
                if plan.reason.startswith("skill_count:")
                else [
                    row["skill_slug"]
                    for row in result.rows
                    if row.get("skill_slug") in {"aws", "azure", "gcp"}
                ]
            )
            try:
                for slug in slugs:
                    docs = await load_documents(
                        self.search.engine, self.search.settings, SearchFilters(skills=[slug])
                    )
                    for doc in docs:
                        if plan.reason == "cloud_data_engineer" and not re.search(
                            r"data engineer", doc.title, re.I
                        ):
                            continue
                        evidence.append(
                            {
                                "job_id": str(doc.job_id),
                                "title": doc.title,
                                "company": doc.company,
                                "location": doc.location,
                                "source_url": doc.source_url,
                                "skill_slug": slug,
                            }
                        )
                        if len(evidence) >= 5:
                            break
                    if len(evidence) >= 5:
                        break
            except (SQLAlchemyError, CorpusTooLarge):
                warnings.append("evidence_unavailable")
        return {
            "response": self.response(
                state,
                status="ok",
                analytics={
                    **asdict(result),
                    "insight_type": plan.reason if state["planner"] == "deterministic" else "",
                },
                evidence=evidence,
                answer=f"Kết quả thống kê: {len(result.rows)} dòng trong bảng analytics. "
                "Số liệu chỉ phản ánh dataset đã thu thập.",
                warnings=warnings,
            )
        }

    async def clarify_node(self, state: AgentState) -> AgentState:
        return {
            "response": self.response(
                state,
                status="needs_clarification",
                answer="Hãy hỏi rõ tìm việc hay thống kê. Ví dụ: ‘Tìm việc Python’, "
                "‘Có bao nhiêu tin nhắc đến Python?’ hoặc ‘Top 10 skills’.",
                warnings=[state["plan"].reason or "question_needs_clarification"],
            )
        }
