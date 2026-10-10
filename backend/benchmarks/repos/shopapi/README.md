# Shop API

Small FastAPI service used as a CodeAtlas retrieval benchmark fixture.

## Running

Set `SHOP_DATABASE_URL` and `SHOP_JWT_SECRET`, then start `uvicorn app.main:app`.
Product images are resized before they are stored in `SHOP_UPLOAD_DIR`.
