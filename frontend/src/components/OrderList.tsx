import React from 'react';
import type { Order } from '../types';
import { StatusBadge } from './StatusBadge';

interface OrderListProps {
  orders: Order[];
  onSelectOrder: (order: Order) => void;
}

export const OrderList: React.FC<OrderListProps> = ({ orders, onSelectOrder }) => {
  const formatDate = (isoString?: string) => {
    if (!isoString) return '-';
    try {
      const d = new Date(isoString);
      return d.toLocaleDateString('en-IN', {
        day: 'numeric',
        month: 'short',
        year: 'numeric',
      });
    } catch {
      return isoString;
    }
  };

  const formatAmount = (amount: number) => {
    return new Intl.NumberFormat('en-IN', {
      style: 'currency',
      currency: 'INR',
      maximumFractionDigits: 0,
    }).format(amount);
  };

  const formatItemsSummary = (order: Order) => {
    if (!order.items || order.items.length === 0) return 'No items';
    const first = order.items[0];
    const firstDesc = `${first.sku} × ${first.quantity}`;
    if (order.items.length > 1) {
      return `${firstDesc} (+${order.items.length - 1} more)`;
    }
    return firstDesc;
  };

  if (orders.length === 0) {
    return (
      <div className="table-card" id="empty-orders-view">
        <div className="state-container">
          <div className="state-icon">📋</div>
          <h3>No Orders Found</h3>
          <p style={{ marginTop: '0.5rem' }}>No orders match the current filter or search criteria.</p>
        </div>
      </div>
    );
  }

  return (
    <div className="table-card" id="orders-table-container">
      <table className="orders-table" id="orders-table">
        <thead>
          <tr>
            <th>Order ID</th>
            <th>Customer</th>
            <th>Items</th>
            <th>Amount</th>
            <th>Status</th>
            <th>Date</th>
          </tr>
        </thead>
        <tbody>
          {orders.map((order) => (
            <tr
              key={order.order_id}
              id={`order-row-${order.order_id}`}
              onClick={() => onSelectOrder(order)}
            >
              <td>
                <span className="order-id-cell">{order.order_id}</span>
              </td>
              <td>
                <div className="customer-cell">
                  <span className="customer-name">
                    {order.customer_name || 'WhatsApp Customer'}
                  </span>
                  <span className="customer-phone">+{order.customer_phone}</span>
                </div>
              </td>
              <td>
                <span className="items-summary-cell">{formatItemsSummary(order)}</span>
              </td>
              <td>
                <span className="amount-cell">{formatAmount(order.grand_total)}</span>
              </td>
              <td>
                <StatusBadge status={order.status} />
              </td>
              <td>
                <span className="date-cell">{formatDate(order.created_at)}</span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
};
