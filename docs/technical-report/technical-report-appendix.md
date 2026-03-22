# Technical Report Appendix: Submission Links, GenAI Logs, and References

This appendix is intended to be attached to the technical report submission package. It keeps the main report concise while still preparing the additional material requested in brief section 7(a).

## A. Submission Links

- **Public GitHub repository:** <https://github.com/FJPez/agent-marketplace>
- **API documentation (Markdown):** <https://github.com/FJPez/agent-marketplace/blob/main/docs/api-reference.md>
- **API documentation (PDF):** <https://github.com/FJPez/agent-marketplace/blob/main/docs/api-reference.pdf>
- **Live Railway deployment URL:** <https://api-production-b705.up.railway.app>
- **Presentation Slides:** <https://docs.google.com/presentation/d/1O4cJ9IYXcOrrxucqrTvgFnUAQfciI4e7zgBCn5c5uDM/edit?usp=sharing>

## B. GenAI Usage Summary

- **Primary AI-assisted planning context:** `codex-agent-plan/PROMPTS/`
- **Idea discussion tool:** ChatGPT web for conversational exploration of approaches and report ideas ([Conversation logs](https://chatgpt.com/share/69c02fc7-a098-800a-a461-9ef58273626f))
- **Coding tool:** Codex agents for repository-based coding, editing, and report artifact generation
- **Workflow structure:** an orchestrator coordinated specialised agents, each given one bounded task in an isolated git worktree before the results were reviewed and merged back into the branch
- **Planning discipline:** each major phase began in plan mode to refine scope and identify key decisions before implementation
- **Minimum skills declared:** `using-superpowers`, `brainstorming`, `writing-plans`
- **Other AI purposes:** architecture exploration, alternative evaluation, debugging support, editing support, and documentation refinement
- **Human oversight statement:** all AI output was reviewed against the coursework brief and the repository before being accepted into the final deliverables

## C. References

[1] FastAPI Documentation. Available at: <https://fastapi.tiangolo.com/>  
[2] Pydantic Documentation. Available at: <https://docs.pydantic.dev/>  
[3] SQLAlchemy Documentation. Available at: <https://docs.sqlalchemy.org/>  
[4] Alembic Documentation. Available at: <https://alembic.sqlalchemy.org/>  
[5] PostgreSQL Documentation. Available at: <https://www.postgresql.org/docs/>  
[6] x402 Documentation. Available at: <https://docs.x402.org/>  
[7] Railway Documentation. Available at: <https://docs.railway.com/>
