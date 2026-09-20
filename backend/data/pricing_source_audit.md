# Pricing Source Audit Report: Corporate Mudhra

**Date:** 2026-09-19  
**Audit Location:** `D:\Mudra Branding Solutions\Corporate Mudhra`  
**Purpose:** Audit and evaluate candidate pricing files to identify Mudhra's current business pricing source of truth vs. historical/catalogue reference archives.

---

## 1. Executive Summary & Recommended Source Candidate

Unlike the single monolithic 2024 archive (`PRICELIST-07.08.2024 EIDTED DATE 10.09.2024.xlsx`), the active `Corporate Mudhra` workspace operates a **decentralized, category-wise master pricing structure** updated between **August and December 2025** (12–16 months newer than the 2024 catalog archive).

### Recommended Source Candidate
The **current pricing source of truth** is the set of **6 category-wise Excel workbooks** located directly inside their respective category folders in `D:\Mudra Branding Solutions\Corporate Mudhra`:

1. `Combos\ALL combos price list .xlsx` (Combos & Gift Sets)
2. `Electronics\ELECTRONICS AUG 2025.xlsx` (Electronics)
3. `Mugs\MUGS.xlsx` (Mugs & Drinkware)
4. `Note Books\NOTEBOOK.xlsx` (Notebooks & Diaries)
5. `Pens\XG - metal pen price list .xlsx` (Writing Instruments)
6. `Water Bottles\WATER BOTTLES.xlsx` (Water Bottles & Sippers)

### Evidence for Recommendation
1. **Recent Modification Timestamps**: All files were modified between August 2025 and December 2025, representing current active pricing:
   - `WATER BOTTLES.xlsx`: `2025-08-23`
   - `XG - metal pen price list .xlsx`: `2025-09-08`
   - `ELECTRONICS AUG 2025.xlsx`: `2025-11-21` (Explicitly labeled "AUG 2025")
   - `ALL combos price list .xlsx`: `2025-11-21`
   - `MUGS.xlsx`: `2025-12-11`
   - `NOTEBOOK.xlsx`: `2025-12-19`
2. **Real Price Revisions Detected**:
   - `XG-MG-003`: was ₹230 in 2024 master list → revised to **₹250** in late 2025.
   - `XG-MG-008`: was ₹150 in 2024 master list → revised to **₹90** in late 2025.
   - `XG-BT-002`: was ₹135 in 2024 master list → revised to **₹145** in late 2025.
   - `XG-T-001` (`XG-EL-001`): was ₹150 in 2024 master list → revised to **₹160** in August 2025.
3. **Missing Prices Resolved**:
   - `NOTEBOOK.xlsx`: In 2024, `XG-NB-001` to `XG-NB-026` had unlisted (`None`) prices. In `NOTEBOOK.xlsx`, all 26 notebooks have live prices (e.g. `XG-NB-001` is ₹165, `XG-NB-002` is ₹145).
   - `XG - metal pen price list .xlsx`: Metal pens (`XG-MP-01` to `XG-MP-62`) were unlisted in 2024. In this 2025 workbook, all pens have live tiered pricing.
4. **Active Formulas & GST Rates**:
   - `WATER BOTTLES.xlsx`: Contains live tax formula `=D4*12/100&" (12%)"` demonstrating **12% GST**.
   - `MUGS.xlsx`: Contains live tax formula `=E4*18/100` demonstrating **18% GST**.
5. **Quantity Tiers Introduced**:
   - `XG - metal pen price list .xlsx` provides explicit quantity tiers (`MOQ 1-299`, `MOQ 300`, `MOQ 500`) and product titles.

---

## 2. Detailed Audit of Candidate Pricing Files

### File 1: Water Bottles
- **File**: `WATER BOTTLES.xlsx`
- **Path**: `D:\Mudra Branding Solutions\Corporate Mudhra\Water Bottles\WATER BOTTLES.xlsx`
- **Size**: 13,434 bytes | **Modified**: 2025-08-23 13:37:11
- **Sheets**: `Sheet1` (87 rows × 6 cols)
- **Headers**: `['SL. NO.', 'PRODUCT CODE', 'COLOR', 'PRICE ( INR )', 'TAX']`
- **Sample Rows**:
  - `[1, 'XG-BT-001', 'B/G/R/BL', 130, '15.6 (12%)']`
  - `[2, 'XG-BT-002', 'B/W/G/R/BL', 145, '17.4 (12%)']`
  - `[3, 'XG-BT-003', 'B/W/G/R', 150, '18 (12%)']`
- **Formulas**: Yes (84 instances of `=D{row}*12/100&" (12%)"`)
- **GST Rate**: **12% GST** (Exclusive in base price)
- **SKUs Present**: Yes (`XG-BT-001` through `XG-BT-083`, 100% match with catalogue)
- **Quantity Tiers**: No (single unit price listed)

### File 2: Mugs & Drinkware
- **File**: `MUGS.xlsx`
- **Path**: `D:\Mudra Branding Solutions\Corporate Mudhra\Mugs\MUGS.xlsx`
- **Size**: 13,241 bytes | **Modified**: 2025-12-11 15:24:10
- **Sheets**: `Sheet1` (61 rows × 6 cols)
- **Headers**: `['SL. NO.', 'PRODUCT CODE', 'COLOR', 'PRICE ( INR )', 'TAX(18%)']`
- **Sample Rows**:
  - `[1, 'XG-MG-001', 'R/W/BL', 95, 17.1]`
  - `[2, 'XG-MG-002', 'B/R/BL', 95, 17.1]`
  - `[3, 'XG-MG-003', 'B/BL/W', 250, 45.0]`
- **Formulas**: Yes (53 instances of `=E{row}*18/100`)
- **GST Rate**: **18% GST** (Exclusive in base price)
- **SKUs Present**: Yes (`XG-MG-001` through `XG-MG-046` plus new items up to `XG-MG-055`)
- **Quantity Tiers**: No (single unit price listed)

### File 3: Writing Instruments (Pens)
- **File**: `XG - metal pen price list .xlsx`
- **Path**: `D:\Mudra Branding Solutions\Corporate Mudhra\Pens\XG - metal pen price list .xlsx`
- **Size**: 13,839 bytes | **Modified**: 2025-09-08 16:11:21
- **Sheets**: `Sheet1` (63 rows × 7 cols)
- **Headers**: `['SL. NO.', 'PRODUCT CODE', 'PRODUCT NAME', 'COLOR', 'MOQ 500', 'MOQ 300', 'MOQ 1-299']`
- **Sample Rows**:
  - `[1, 'XG-MP 01', 'ALUMINIUM METAL', 'BK/BL/R/G/MG/WH', 12.5, 13.5, 15]`
  - `[2, 'XG-MP 02', 'BAMBOO GRIP (P 52)', 'BK/R/ BL', 19, 20, 23]`
  - `[3, 'XG-MP 03', 'RUBBER COATED (P 2)', 'BK/R/ BL', 18, 20, 23]`
- **Formulas**: None (hardcoded tier values)
- **GST Rate**: **18% GST** (Standard stationery GST; exclusive)
- **SKUs Present**: Yes (`XG-MP 01` through `XG-MP 62`)
- **Quantity Tiers**: **YES** (Tier 1: 1–299, Tier 2: 300–499, Tier 3: 500+)

### File 4: Notebooks & Diaries
- **File**: `NOTEBOOK.xlsx`
- **Path**: `D:\Mudra Branding Solutions\Corporate Mudhra\Note Books\NOTEBOOK.xlsx`
- **Size**: 13,491 bytes | **Modified**: 2025-12-19 16:35:15
- **Sheets**: `Sheet1` (77 rows × 5 cols), `Sheet2` (empty)
- **Headers**: `['SL. NO.', 'PRODUCT CODE', 'COLOR', 'PRICE ( INR )']`
- **Sample Rows**:
  - `[1, 'XG-NB-001', 'B/W/BL/R/GREY', 165]`
  - `[2, 'XG-NB-002', 'B/W/BL/R/GREY', 145]`
  - `[3, 'XG-NB-003', 'B/W/BL/R/GREY', 120]`
- **Formulas**: None
- **GST Rate**: **18% GST** (Standard stationery GST; exclusive)
- **SKUs Present**: Yes (`XG-NB-001` through `XG-NB-026` plus new extensions up to `XG-NB-072`)
- **Quantity Tiers**: No (single unit price listed)

### File 5: Electronics
- **File**: `ELECTRONICS AUG 2025.xlsx`
- **Path**: `D:\Mudra Branding Solutions\Corporate Mudhra\Electronics\ELECTRONICS AUG 2025.xlsx`
- **Size**: 13,200 bytes | **Modified**: 2025-11-21 19:17:41
- **Sheets**: `ELECTRONICS` (79 rows × 14 cols)
- **Headers**: `['SL. NO.', 'PRODUCT CODE', 'COLOR', 'PRICE ( INR )']`
- **Sample Rows**:
  - `[1, 'XG-T-001', 'B/W', 160]` (Maps to `XG-EL-001`, was ₹150 in 2024)
  - `[2, 'XG-T-002', 'B/R/BL', 125]`
  - `[10, 'XG-T-010', 'W/B/G', 400]` (Maps to `XG-EL-010`, was ₹385 in 2024)
- **Formulas**: None
- **GST Rate**: **18% GST** (Electronic gadgets; exclusive)
- **SKUs Present**: Yes (44 match catalogue items under `XG-T-` prefix instead of `XG-EL-`, plus 27 new items up to `XG-T-071`)
- **Quantity Tiers**: No (single unit price listed)

### File 6: Combos & Gift Sets
- **File**: `ALL combos price list .xlsx`
- **Path**: `D:\Mudra Branding Solutions\Corporate Mudhra\Combos\ALL combos price list .xlsx`
- **Size**: 43,594 bytes | **Modified**: 2025-11-21 19:17:42
- **Sheets**: `ALL COMBOS` (175 rows × 46 cols), `2 IN 1`, `3 IN 1`, `4 IN 1`, `5 IN 1 `, `6 IN 1`, `7 IN 1`
- **Headers**: Multi-column tabular layout per combo combination with `PRODUCT CODE`, `GIFT SET` or `COLOR`, `PRICE`
- **Sample Rows**:
  - `XG-GS-267`: ₹255 (2-in-1 Gift Set)
  - `XG - GS - 001`: ₹445 (3-in-1 Gift Set)
  - `XG-GS-575`: ₹620 (3-in-1 Gift Set, corresponds to `XG-575`)
  - `XG - GS - 008`: ₹765 (4-in-1 Gift Set)
  - `XG - GS - 015`: ₹525 (5-in-1 Gift Set)
  - `XG-GS-584`: ₹990 (7-in-1 Gift Set, corresponds to `XG-584`)
- **Formulas**: None
- **GST Rate**: **18% GST** (Exclusive in base price)
- **SKUs Present**: Yes (424 combo configurations)
- **Quantity Tiers**: No (single unit price per set)

---

## 3. Comparative Summary Matrix

| Category | Work File (Corporate Mudhra) | Last Modified | Total Items | GST Rate | Quantity Tiers Available? |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Water Bottles** | `WATER BOTTLES.xlsx` | 2025-08-23 | 83 | **12%** | Single base price |
| **Writing Instruments** | `XG - metal pen price list .xlsx` | 2025-09-08 | 62 | **18%** | **YES** (1-299, 300-499, 500+) |
| **Electronics** | `ELECTRONICS AUG 2025.xlsx` | 2025-11-21 | 71 | **18%** | Single base price |
| **Combos & Gift Sets** | `ALL combos price list .xlsx` | 2025-11-21 | 424 | **18%** | Single base price |
| **Mugs & Drinkware** | `MUGS.xlsx` | 2025-12-11 | 53 | **18%** | Single base price |
| **Notebooks** | `NOTEBOOK.xlsx` | 2025-12-19 | 65 | **18%** | Single base price |

---

## 4. Key Pricing Patterns & Business Rules Discovered

1. **GST Handling**:
   - **All listed prices across all files are GST-exclusive**.
   - **Tax Rates Differ by Category**: Water Bottles are explicitly taxed at **12% GST** (`=D4*12/100`), while Mugs and other items are taxed at **18% GST** (`=E4*18/100`).
2. **Volume Tiers vs. Fixed Base Price**:
   - **Pens** are the only category with an explicit, item-by-item volume discounting tier table (`1-299`, `300`, `500+`).
   - Other categories (Mugs, Bottles, Notebooks, Gift Sets) maintain a single base unit price in these files.
3. **SKU Evolution & Prefix Variations**:
   - `Electronics`: Uses `XG-T-001` through `XG-T-044` instead of `XG-EL-001` through `XG-EL-044`.
   - `Combos`: Uses `XG-GS-xxx` or `XG - GS - xxx` for combo gift sets.
   - `Pens`: Uses `XG-MP 01` (space) instead of `XG-MP-01` (hyphen).

---

## 5. Unresolved Questions Before Implementation

1. **Google Sheet Linkage**: Does Mudhra maintain an online Google Sheet that pulls or pushes to these 2025 Excel files, or are these 6 local Excel workbooks the primary source of truth? If an online Google Sheet exists, what is its URL/Spreadsheet ID?
2. **Volume Tier Rule for Non-Pen Categories**: For categories with only a single base price (Mugs, Bottles, Notebooks, Combos), does Mudhra apply a standard volume discount formula (e.g. 5% off for 100+, 10% off for 250+), or must non-tiered categories quote only custom sales pricing?
3. **SKU Prefix Normalization**: Should the pricing engine automatically equate `XG-T-xxx` with `XG-EL-xxx`, `XG-GS-501` with `XG-501`, and `XG-MP 01` with `XG-MP-01`?
4. **GST Rates**: Should our `PricingService` enforce category-specific GST defaults (12% for Water Bottles, 18% for all others)?
