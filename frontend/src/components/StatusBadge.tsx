import React from 'react';
import type { OrderStatus } from '../types';

interface StatusBadgeProps {
  status: OrderStatus | string;
}

export const StatusBadge: React.FC<StatusBadgeProps> = ({ status }) => {
  let badgeClass = 'badge-confirmed';
  let label = status;

  switch (status) {
    case 'PENDING_CONFIRMATION':
      badgeClass = 'badge-pending';
      label = 'Pending Confirmation';
      break;
    case 'CONFIRMED':
      badgeClass = 'badge-confirmed';
      label = 'Confirmed';
      break;
    case 'PROCESSING':
      badgeClass = 'badge-processing';
      label = 'Processing';
      break;
    case 'READY_FOR_DISPATCH':
      badgeClass = 'badge-ready';
      label = 'Ready for Dispatch';
      break;
    case 'DISPATCHED':
      badgeClass = 'badge-dispatched';
      label = 'Dispatched';
      break;
    case 'DELIVERED':
      badgeClass = 'badge-delivered';
      label = 'Delivered';
      break;
    case 'CANCELLED':
      badgeClass = 'badge-cancelled';
      label = 'Cancelled';
      break;
  }

  return (
    <span className={`status-badge ${badgeClass}`} id={`status-badge-${status.toLowerCase()}`}>
      <span className="status-badge-dot" />
      {label}
    </span>
  );
};
