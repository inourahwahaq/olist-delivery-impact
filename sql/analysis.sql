-- Delivery performance and customer satisfaction, January 2017 to August 2018
-- Engine: SQLite

-- One review per order: the latest one
DROP TABLE IF EXISTS latest_review;
CREATE TABLE latest_review AS
SELECT order_id, review_score, review_creation_date
FROM (
    SELECT
        order_id,
        review_score,
        review_creation_date,
        ROW_NUMBER() OVER (
            PARTITION BY order_id
            ORDER BY review_creation_date DESC, review_answer_timestamp DESC
        ) AS review_rank
    FROM order_reviews
)
WHERE review_rank = 1;

-- Order value, and the most expensive item decides the order's category and seller
DROP TABLE IF EXISTS order_value;
CREATE TABLE order_value AS
SELECT
    order_id,
    COUNT(*) AS items,
    SUM(price) AS price,
    SUM(freight_value) AS freight
FROM order_items
GROUP BY order_id;

DROP TABLE IF EXISTS main_item;
CREATE TABLE main_item AS
SELECT order_id, product_id, seller_id
FROM (
    SELECT
        order_id,
        product_id,
        seller_id,
        ROW_NUMBER() OVER (PARTITION BY order_id ORDER BY price DESC, order_item_id) AS item_rank
    FROM order_items
)
WHERE item_rank = 1;

-- Delivered orders in the study period, with logical dates
DROP TABLE IF EXISTS delivered_orders;
CREATE TABLE delivered_orders AS
SELECT
    o.order_id,
    c.customer_unique_id,
    c.customer_state,
    s.seller_state,
    COALESCE(n.product_category_name_english, 'unknown') AS category,
    substr(o.order_purchase_timestamp, 1, 7) AS month,
    o.order_purchase_timestamp AS purchase_time,
    o.order_delivered_customer_date AS delivered_time,
    o.order_estimated_delivery_date AS estimated_date,
    julianday(date(o.order_delivered_customer_date)) - julianday(date(o.order_estimated_delivery_date)) AS days_after_estimate,
    julianday(o.order_delivered_customer_date) - julianday(o.order_purchase_timestamp) AS delivery_days,
    date(o.order_delivered_customer_date) > date(o.order_estimated_delivery_date) AS is_late,
    c.customer_state = s.seller_state AS same_state,
    v.items,
    v.price,
    v.freight,
    r.review_score,
    date(r.review_creation_date) < date(o.order_delivered_customer_date) AS review_before_delivery
FROM orders o
JOIN customers c ON c.customer_id = o.customer_id
JOIN order_value v ON v.order_id = o.order_id
JOIN main_item m ON m.order_id = o.order_id
JOIN sellers s ON s.seller_id = m.seller_id
JOIN products p ON p.product_id = m.product_id
LEFT JOIN category_names n ON n.product_category_name = p.product_category_name
LEFT JOIN latest_review r ON r.order_id = o.order_id
WHERE o.order_status = 'delivered'
  AND o.order_delivered_customer_date IS NOT NULL
  AND o.order_purchase_timestamp >= '2017-01-01'
  AND o.order_purchase_timestamp < '2018-09-01'
  AND o.order_delivered_carrier_date >= o.order_purchase_timestamp
  AND o.order_delivered_customer_date >= o.order_delivered_carrier_date;

-- @export data_quality
SELECT 'all orders' AS step, COUNT(*) AS orders FROM orders
UNION ALL SELECT 'delivered with a delivery date', COUNT(*) FROM orders WHERE order_status = 'delivered' AND order_delivered_customer_date IS NOT NULL
UNION ALL SELECT 'orders in the study', COUNT(*) FROM delivered_orders
UNION ALL SELECT 'orders with a review', COUNT(review_score) FROM delivered_orders
UNION ALL SELECT 'reviews written before delivery', COUNT(*) FROM delivered_orders WHERE review_before_delivery = 1;

-- @export summary
SELECT
    COUNT(*) AS orders,
    ROUND(100.0 * AVG(is_late), 2) AS late_pct,
    ROUND(AVG(delivery_days), 1) AS avg_delivery_days,
    ROUND(SUM(price)) AS revenue,
    ROUND(SUM(CASE WHEN is_late = 1 THEN price ELSE 0 END)) AS late_revenue
FROM delivered_orders;

-- @export late_vs_review
SELECT
    is_late,
    COUNT(*) AS reviews,
    ROUND(AVG(review_score), 2) AS avg_score,
    ROUND(100.0 * AVG(review_score <= 2), 1) AS low_score_pct
FROM delivered_orders
WHERE review_score IS NOT NULL
GROUP BY is_late;

-- @export review_timing
SELECT
    is_late,
    review_before_delivery,
    COUNT(*) AS reviews,
    ROUND(AVG(review_score), 2) AS avg_score
FROM delivered_orders
WHERE review_score IS NOT NULL
GROUP BY is_late, review_before_delivery;

-- @export delay_level
SELECT
    CASE
        WHEN days_after_estimate <= -15 THEN '1. 15+ days early'
        WHEN days_after_estimate <= -8 THEN '2. 8-14 days early'
        WHEN days_after_estimate <= 0 THEN '3. 0-7 days early'
        WHEN days_after_estimate <= 3 THEN '4. 1-3 days late'
        WHEN days_after_estimate <= 7 THEN '5. 4-7 days late'
        WHEN days_after_estimate <= 14 THEN '6. 8-14 days late'
        ELSE '7. 15+ days late'
    END AS delay_level,
    COUNT(*) AS reviews,
    ROUND(AVG(review_score), 2) AS avg_score,
    ROUND(100.0 * AVG(review_score <= 2), 1) AS low_score_pct
FROM delivered_orders
WHERE review_score IS NOT NULL
GROUP BY delay_level
ORDER BY delay_level;

-- @export monthly
WITH monthly AS (
    SELECT
        month,
        COUNT(*) AS orders,
        SUM(is_late) AS late_orders,
        SUM(price) AS revenue
    FROM delivered_orders
    GROUP BY month
)
SELECT
    month,
    orders,
    late_orders,
    ROUND(100.0 * late_orders / orders, 1) AS late_pct,
    ROUND(revenue) AS revenue,
    ROUND(100.0 * (orders - LAG(orders) OVER (ORDER BY month)) / LAG(orders) OVER (ORDER BY month), 1) AS orders_change_pct
FROM monthly
ORDER BY month;

-- @export customer_states
WITH states AS (
    SELECT
        customer_state,
        COUNT(*) AS orders,
        SUM(is_late) AS late_orders,
        SUM(price) AS revenue,
        AVG(delivery_days) AS avg_days
    FROM delivered_orders
    GROUP BY customer_state
)
SELECT
    customer_state,
    orders,
    late_orders,
    ROUND(100.0 * late_orders / orders, 1) AS late_pct,
    ROUND(avg_days, 1) AS avg_delivery_days,
    ROUND(revenue) AS revenue,
    ROUND(100.0 * late_orders / SUM(late_orders) OVER (), 1) AS share_of_late_pct,
    ROUND(100.0 * SUM(late_orders) OVER (ORDER BY late_orders DESC ROWS UNBOUNDED PRECEDING) / SUM(late_orders) OVER (), 1) AS running_share_pct,
    RANK() OVER (ORDER BY late_orders DESC) AS late_rank
FROM states
ORDER BY late_rank;

-- @export routes
SELECT
    seller_state,
    customer_state,
    COUNT(*) AS orders,
    SUM(is_late) AS late_orders,
    ROUND(100.0 * AVG(is_late), 1) AS late_pct,
    ROUND(AVG(delivery_days), 1) AS avg_delivery_days
FROM delivered_orders
GROUP BY seller_state, customer_state
HAVING COUNT(*) >= 200
ORDER BY late_orders DESC;

-- @export categories
SELECT
    category,
    COUNT(*) AS orders,
    SUM(is_late) AS late_orders,
    ROUND(100.0 * AVG(is_late), 1) AS late_pct,
    ROUND(SUM(price)) AS revenue,
    ROUND(AVG(review_score), 2) AS avg_score
FROM delivered_orders
GROUP BY category
HAVING COUNT(*) >= 300
ORDER BY late_orders DESC;

-- @export repeat_customers
WITH customer_orders AS (
    SELECT customer_unique_id, COUNT(*) AS orders
    FROM delivered_orders
    GROUP BY customer_unique_id
)
SELECT
    COUNT(*) AS customers,
    SUM(orders > 1) AS repeat_customers,
    ROUND(100.0 * AVG(orders > 1), 2) AS repeat_pct
FROM customer_orders;
