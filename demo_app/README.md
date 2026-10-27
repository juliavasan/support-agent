# demo_app

The **demo system**: ERPNext (open-source ERP), run in Docker and pinned to a version that predates the fixes for the seeded bugs.

Planned contents (Phase 5):
- `docker-compose.yml` based on the official `frappe_docker` images, with a pinned ERPNext version
- Fixtures for the fictional company *Brightline Supplies* (currencies, suppliers, accounts, tax templates), loaded through the ERPNext REST API
- `BUGS.md`: which seed incidents reproduce on the pinned version, and how

See [docs/ARCHITECTURE.md](../docs/ARCHITECTURE.md), §6.
