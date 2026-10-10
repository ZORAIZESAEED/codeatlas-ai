from fastapi import APIRouter, Depends, HTTPException, UploadFile

from app.db.models import Product
from app.db.session import get_session
from app.images.preprocess import save_product_image
from app.schemas import ProductCreate

router = APIRouter(prefix="/products", tags=["products"])


@router.get("")
def list_products(limit: int = 50, offset: int = 0, session=Depends(get_session)):
    return session.query(Product).order_by(Product.id).offset(offset).limit(limit).all()


@router.post("", status_code=201)
def create_product(payload: ProductCreate, session=Depends(get_session)):
    """Add a new product to the catalogue."""
    product = Product(name=payload.name, price=payload.price, stock=payload.stock)
    session.add(product)
    session.commit()
    return product


@router.get("/{product_id}")
def get_product(product_id: int, session=Depends(get_session)):
    product = session.get(Product, product_id)
    if product is None:
        raise HTTPException(status_code=404, detail="Product not found")
    return product


@router.post("/{product_id}/image")
def upload_product_image(product_id: int, file: UploadFile, session=Depends(get_session)):
    product = session.get(Product, product_id)
    if product is None:
        raise HTTPException(status_code=404, detail="Product not found")
    product.image_path = save_product_image(product_id, file.file.read())
    session.commit()
    return {"image_path": product.image_path}
