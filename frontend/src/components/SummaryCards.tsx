import React from 'react';
import type { Order } from '../types';

interface SummaryCardsProps {
  orders: Order[];
  activeFilter: string;
  onSelectFilter: (status: string) => void;
  pendingQuotesCount?: number;
  onSelectEnquiries?: () => void;
}

export const SummaryCards: React.FC<SummaryCardsProps> = ({
  orders,
  activeFilter,
  onSelectFilter,
  pendingQuotesCount = 0,
  onSelectEnquiries,
}) => {
  const totalCount = orders.length;
  const totalValue = orders.reduce((acc, o) => acc + (o.grand_total || 0), 0);
  const confirmedCount = orders.filter((o) => o.status === 'CONFIRMED').length;
  const processingCount = orders.filter((o) => o.status === 'PROCESSING').length;
  const readyCount = orders.filter((o) => o.status === 'READY_FOR_DISPATCH' || o.status === 'DISPATCHED').length;
  const deliveredCount = orders.filter((o) => o.status === 'DELIVERED').length;
  const cancelledCount = orders.filter((o) => o.status === 'CANCELLED').length;

  const formatCurrencyCompact = (amount: number) => {
    if (amount >= 10000000) {
      return `₹${(amount / 10000000).toFixed(2)} Cr`;
    }
    if (amount >= 100000) {
      return `₹${(amount / 100000).toFixed(2)} L`;
    }
    if (amount >= 1000) {
      return `₹${(amount / 1000).toFixed(1)} K`;
    }
    return `₹${Math.round(amount).toLocaleString('en-IN')}`;
  };

  const cards = [
    {
      id: 'card-total-orders',
      title: 'Total Orders',
      value: totalCount,
      filter: 'ALL',
      indicatorClass: 'indicator-total',
      onClick: () => onSelectFilter('ALL'),
    },
    {
      id: 'card-total-value',
      title: 'Total Order Value',
      value: formatCurrencyCompact(totalValue),
      filter: 'VALUE',
      indicatorClass: 'indicator-value',
      onClick: () => onSelectFilter('ALL'),
    },
    {
      id: 'card-confirmed-orders',
      title: 'Confirmed',
      value: confirmedCount,
      filter: 'CONFIRMED',
      indicatorClass: 'indicator-confirmed',
      onClick: () => onSelectFilter('CONFIRMED'),
    },
    {
      id: 'card-processing-orders',
      title: 'Processing',
      value: processingCount,
      filter: 'PROCESSING',
      indicatorClass: 'indicator-processing',
      onClick: () => onSelectFilter('PROCESSING'),
    },
    {
      id: 'card-ready-orders',
      title: 'Ready / Dispatched',
      value: readyCount,
      filter: 'READY_FOR_DISPATCH',
      indicatorClass: 'indicator-ready',
      onClick: () => onSelectFilter('READY_FOR_DISPATCH'),
    },
    {
      id: 'card-delivered-orders',
      title: 'Delivered',
      value: deliveredCount,
      filter: 'DELIVERED',
      indicatorClass: 'indicator-delivered',
      onClick: () => onSelectFilter('DELIVERED'),
    },
    {
      id: 'card-cancelled-orders',
      title: 'Cancelled',
      value: cancelledCount,
      filter: 'CANCELLED',
      indicatorClass: 'indicator-cancelled',
      onClick: () => onSelectFilter('CANCELLED'),
    },
  ];

  if (pendingQuotesCount > 0 && onSelectEnquiries) {
    cards.push({
      id: 'card-pending-quotes',
      title: 'Pending Quotes',
      value: pendingQuotesCount,
      filter: 'ENQUIRIES',
      indicatorClass: 'indicator-enquiries',
      onClick: onSelectEnquiries,
    });
  }

  return (
    <div className="summary-grid" id="summary-cards-container">
      {cards.map((card) => {
        const isActive = activeFilter === card.filter;
        return (
          <div
            key={card.id}
            id={card.id}
            className={`summary-card ${isActive ? 'active' : ''}`}
            onClick={card.onClick}
            title={`Filter by ${card.title}`}
          >
            <div className={`card-indicator ${card.indicatorClass}`} />
            <div className="card-title">{card.title}</div>
            <div className="card-value">{card.value}</div>
          </div>
        );
      })}
    </div>
  );
};
