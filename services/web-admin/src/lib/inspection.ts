import type { PhotoInspection } from "./types";

// Ракурс → подпись и поле file_id в DTO (null = фото нет, напр. бока у старых осмотров).
export const INSPECTION_ANGLES: { key: string; label: string; field: keyof PhotoInspection }[] = [
  { key: "front", label: "Перёд", field: "front_file_id" },
  { key: "left", label: "Левый бок", field: "left_file_id" },
  { key: "rear", label: "Зад", field: "rear_file_id" },
  { key: "right", label: "Правый бок", field: "right_file_id" },
  { key: "interior_front", label: "Салон спереди", field: "interior_front_file_id" },
  { key: "interior_rear", label: "Салон сзади", field: "interior_rear_file_id" },
];
