"""
Product Image Import & Supabase Storage Mapping Script
Maps 297 real product images from local directory to canonical catalogue SKUs,
uploads them to the Supabase Storage 'product-images' bucket, and updates products.image_url.

Preserves canonical customer-facing SKUs (e.g. XG-GS-061.jpg -> GS-061).
Strictly skips unmatched images (e.g. XG-BT-123.jpg, XG-MG-048.jpg).
"""

import argparse
import json
import os
import sys
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from dotenv import load_dotenv
load_dotenv()

DEFAULT_IMAGES_DIR = r"C:\Users\admin\Desktop\Chat Agent\public\images\products"
DEFAULT_CATALOGUE_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "data", "catalogue_master.json")
)
STORAGE_BUCKET_NAME = "product-images"


def map_images(images_dir: str, catalogue_path: str) -> Tuple[Dict[str, Dict[str, str]], List[str]]:
    """
    Discovers local images and maps them to canonical catalogue SKUs.
    Returns (mapped_dict, unmapped_list).
    """
    if not os.path.exists(catalogue_path):
        raise FileNotFoundError(f"Catalogue master file not found: {catalogue_path}")

    with open(catalogue_path, "r", encoding="utf-8") as f:
        cat_data = json.load(f)

    cat_skus = {item["sku"]: item for item in cat_data if item.get("sku")}

    if not os.path.exists(images_dir):
        raise FileNotFoundError(f"Product images directory not found: {images_dir}")

    files = [f for f in os.listdir(images_dir) if f.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))]

    mapped: Dict[str, Dict[str, str]] = {}
    unmapped: List[str] = []

    for filename in sorted(files):
        base, ext = os.path.splitext(filename)
        canonical_sku = None

        # 1. Direct SKU match (e.g. XG-BT-001)
        if base in cat_skus:
            canonical_sku = base
        # 2. Prefix match where image has XG- prefix but catalogue has GS-xxx (e.g. XG-GS-061 -> GS-061)
        elif base.startswith("XG-GS-") and base[3:] in cat_skus:
            canonical_sku = base[3:]
        # 3. Alternate prefix where image lacks XG- (e.g. BT-001 -> XG-BT-001)
        elif f"XG-{base}" in cat_skus:
            canonical_sku = f"XG-{base}"

        if canonical_sku:
            mapped[canonical_sku] = {
                "filename": filename,
                "source_path": os.path.join(images_dir, filename),
                "canonical_sku": canonical_sku,
                "storage_object": f"{canonical_sku}{ext.lower()}",
            }
        else:
            unmapped.append(filename)

    return mapped, unmapped


def generate_images_sql_migration(mapped: Dict[str, Dict[str, str]], supabase_url: str, output_path: str) -> None:
    """Generates an idempotent SQL migration file that updates products.image_url."""
    base_storage_url = f"{supabase_url.rstrip('/')}/storage/v1/object/public/{STORAGE_BUCKET_NAME}"
    lines = [
        "-- =============================================================================",
        "-- Migration: 003_update_product_images.sql",
        f"-- Generated: {len(mapped)} Mapped Product Images",
        f"-- Storage Bucket: {STORAGE_BUCKET_NAME}",
        "-- =============================================================================",
        "",
        "BEGIN;",
        "",
    ]

    for sku, info in sorted(mapped.items()):
        public_url = f"{base_storage_url}/{info['storage_object']}"
        lines.append(f"UPDATE products SET image_url = '{public_url}', updated_at = TIMEZONE('utc'::text, NOW()) WHERE sku = '{sku}';")

    lines.append("")
    lines.append("COMMIT;")
    lines.append("")

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def main():
    parser = argparse.ArgumentParser(description="Upload & map real product images to Supabase Storage")
    parser.add_argument("--images-dir", default=DEFAULT_IMAGES_DIR, help="Local directory containing product images")
    parser.add_argument("--catalogue", default=DEFAULT_CATALOGUE_PATH, help="Path to catalogue_master.json")
    parser.add_argument("--dry-run", action="store_true", help="Run mapping and validation without uploading")
    args = parser.parse_args()

    print("=" * 65)
    print("MUDHRA B2B PLATFORM — PRODUCT IMAGE MAPPING & STORAGE UPLOADER")
    print("=" * 65)

    mapped, unmapped = map_images(args.images_dir, args.catalogue)

    print(f"\n[IMAGE DISCOVERY RESULT]")
    print(f"  * Source Directory:    {args.images_dir}")
    print(f"  * Total Images Found:  {len(mapped) + len(unmapped)}")
    print(f"  * Successfully Mapped: {len(mapped)} / {len(mapped) + len(unmapped)} ({(len(mapped)/(len(mapped) + len(unmapped)))*100:.1f}%)")
    print(f"  * Unmapped (Skipped):  {len(unmapped)}")
    for u in unmapped:
        print(f"      - {u}")

    supabase_url = os.getenv("SUPABASE_URL", "https://your-project.supabase.co")
    migration_path = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "migrations", "003_update_product_images.sql")
    )
    generate_images_sql_migration(mapped, supabase_url, migration_path)
    print(f"\n[MIGRATION GENERATED]")
    print(f"  * Generated SQL file: {migration_path}")
    print(f"  * Contains {len(mapped)} UPDATE statements for products.image_url")

    service_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_KEY")
    if not service_key or os.getenv("SUPABASE_URL") is None or args.dry_run:
        print("\n[READY FOR SUPABASE STORAGE]")
        print("  * Dry-run / Offline mode complete.")
        print(f"  * When SUPABASE_URL & SUPABASE_SERVICE_ROLE_KEY are set:")
        print(f"      1. Create public bucket '{STORAGE_BUCKET_NAME}' in Supabase.")
        print(f"      2. Run this script without --dry-run to upload all {len(mapped)} images.")
        print("      3. Execute migrations/003_update_product_images.sql in Supabase SQL Editor.")
        return 0

    import httpx
    print(f"\n[CONNECTING TO SUPABASE STORAGE]")
    headers = {
        "Authorization": f"Bearer {service_key}",
        "apikey": service_key,
    }

    with httpx.Client(timeout=30.0) as client:
        # 1. Ensure bucket exists
        bucket_url = f"{supabase_url.rstrip('/')}/storage/v1/bucket"
        res = client.get(bucket_url, headers=headers)
        existing_buckets = [b.get("name") for b in res.json()] if res.is_success and isinstance(res.json(), list) else []
        
        if STORAGE_BUCKET_NAME not in existing_buckets:
            print(f"  * Creating bucket '{STORAGE_BUCKET_NAME}' (public: true)...")
            res_create = client.post(
                bucket_url,
                headers=headers,
                json={"id": STORAGE_BUCKET_NAME, "name": STORAGE_BUCKET_NAME, "public": True}
            )
            if not res_create.is_success:
                print(f"    [WARN] Bucket creation response: {res_create.status_code} {res_create.text}")

        # 2. Upload images
        print(f"  * Uploading {len(mapped)} images to bucket '{STORAGE_BUCKET_NAME}'...")
        uploaded = 0
        for sku, info in mapped.items():
            obj_name = info["storage_object"]
            upload_url = f"{supabase_url.rstrip('/')}/storage/v1/object/{STORAGE_BUCKET_NAME}/{obj_name}"
            with open(info["source_path"], "rb") as img_file:
                content = img_file.read()
            
            upload_headers = dict(headers)
            upload_headers["Content-Type"] = "image/jpeg"
            upload_headers["x-upsert"] = "true"

            up_res = client.post(upload_url, headers=upload_headers, content=content)
            if up_res.is_success:
                uploaded += 1
            else:
                print(f"    [WARN] Failed to upload {obj_name}: {up_res.status_code}")

        print(f"\n[STORAGE UPLOAD COMPLETE] {uploaded} / {len(mapped)} images uploaded successfully.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
